import { expect } from "chai";
import { network } from "hardhat";
import Safe from "@safe-global/protocol-kit";
import { deployLocalSafeInfra, localTestWallet } from "../scripts/_safeLocalInfra.js";

const connection = await network.create();
const { ethers } = connection;

/**
 * Proves the actual point of moving YouthChainRegistry's ownership to a
 * Safe (see the engineering audit finding this closes): that governance
 * genuinely requires multiple confirmations, not just that a Safe object
 * exists pointed at the registry. YouthChainRegistry.test.js already
 * covers the registry's own access control in isolation (only owner()
 * can call accreditIssuer/revokeIssuer/revokeCredential) -- this file
 * covers the layer on top: once owner() IS a Safe, what does it actually
 * take to exercise that access.
 */
describe("YouthChainRegistry ownership via a Safe multisig", function () {
    this.timeout(120000);

    let registry;
    let safe; // protocol-kit instance connected with owner 0's signer
    let owner0Address, owner1Address, owner2Address;
    let deployerSigner;

    before(async function () {
        [deployerSigner] = await ethers.getSigners();

        owner0Address = localTestWallet(ethers, 0).address;
        owner1Address = localTestWallet(ethers, 1).address;
        owner2Address = localTestWallet(ethers, 2).address;

        // Deploy the real Safe v1.4.1 core contracts fresh, same as
        // scripts/deploySafe.js does for any local/throwaway network --
        // see _safeLocalInfra.js's own docstring for why this is
        // necessary here but never on a real public network.
        const contractNetworks = await deployLocalSafeInfra(ethers, deployerSigner);

        const protocolKit = await Safe.init({
            provider: connection.provider,
            signer: localTestWallet(ethers, 0).privateKey,
            predictedSafe: {
                safeAccountConfig: {
                    owners: [owner0Address, owner1Address, owner2Address],
                    threshold: 2,
                },
                safeDeploymentConfig: { safeVersion: "1.4.1" },
            },
            contractNetworks,
        });

        const safeAddress = await protocolKit.getAddress();
        const deploymentTransaction = await protocolKit.createSafeDeploymentTransaction();
        const txResponse = await deployerSigner.sendTransaction({
            to: deploymentTransaction.to,
            value: deploymentTransaction.value,
            data: deploymentTransaction.data,
        });
        await txResponse.wait();

        safe = await protocolKit.connect({ safeAddress });
        expect(await safe.isSafeDeployed()).to.equal(true);
        expect(await safe.getThreshold()).to.equal(2);

        // Deploy the registry with THIS Safe as its initial owner directly
        // (the clean path scripts/deploy.js's OWNER_ADDRESS override
        // supports for a fresh deployment) rather than deploy-then-
        // transfer -- that two-step path is covered separately below by
        // the "migrating an already-deployed registry" describe block.
        const Registry = await ethers.getContractFactory("YouthChainRegistry");
        registry = await Registry.deploy(safeAddress);
        await registry.waitForDeployment();

        expect(await registry.owner()).to.equal(safeAddress);
    });

    it("a single Safe owner's confirmation alone is NOT enough to execute a governance action", async function () {
        const strangerAddress = ethers.Wallet.createRandom().address;
        const data = registry.interface.encodeFunctionData("accreditIssuer", [strangerAddress]);

        const safeTransaction = await safe.createTransaction({
            transactions: [{ to: await registry.getAddress(), value: "0", data }],
        });
        const signedBySafe0 = await safe.signTransaction(safeTransaction);

        // Only one signature on a 2-of-3 Safe -- isValidTransaction is
        // this SDK's own pre-flight check for exactly this, and
        // executeTransaction must actually revert too, not just this
        // check agree with it, since isValidTransaction and
        // executeTransaction go through genuinely different code paths.
        expect(await safe.isValidTransaction(signedBySafe0)).to.equal(false);
        await expect(safe.executeTransaction(signedBySafe0)).to.be.rejected;

        // And provably not just "the transaction failed for some other
        // reason" -- the stranger address must still not be accredited.
        expect(await registry.accreditedIssuers(strangerAddress)).to.equal(false);
    });

    it("2 of 3 Safe owners' confirmations execute the governance action for real", async function () {
        const targetAddress = ethers.Wallet.createRandom().address;
        const data = registry.interface.encodeFunctionData("accreditIssuer", [targetAddress]);

        const safeTransaction = await safe.createTransaction({
            transactions: [{ to: await registry.getAddress(), value: "0", data }],
        });

        // Owner 0 signs (via `safe`, already connected as owner 0's
        // signer), then owner 1 signs the SAME transaction via a second
        // protocol-kit instance connected as owner 1 -- a real second,
        // independent signer, not the same key signing twice. On a real
        // Safe, this second signature comes from a different human
        // entirely, in their own session (the Safe UI at app.safe.global,
        // or their own independent script run) -- simulated here within
        // one test process only because this is a local demo where
        // proving the end-to-end flow matters more than mirroring exactly
        // how two separate humans would coordinate it.
        const signedByOwner0 = await safe.signTransaction(safeTransaction);
        const safeAsOwner1 = await safe.connect({ signer: localTestWallet(ethers, 1).privateKey });
        const signedByBoth = await safeAsOwner1.signTransaction(signedByOwner0);

        expect(await safeAsOwner1.isValidTransaction(signedByBoth)).to.equal(true);
        const result = await safeAsOwner1.executeTransaction(signedByBoth);
        await result.transactionResponse.wait();

        expect(await registry.accreditedIssuers(targetAddress)).to.equal(true);
    });

    describe("migrating an already-deployed registry (single EOA owner) to a Safe", function () {
        it("transferOwnership + acceptOwnership hands control to the Safe, two-step, and the Safe's own threshold governs it", async function () {
            // A registry that started with a plain EOA owner, same as
            // every deployment before this Safe integration existed.
            const Registry = await ethers.getContractFactory("YouthChainRegistry");
            const legacyRegistry = await Registry.deploy(deployerSigner.address);
            await legacyRegistry.waitForDeployment();
            expect(await legacyRegistry.owner()).to.equal(deployerSigner.address);

            // Step 1 (old owner, Ownable2Step's own first half): NOT yet
            // transferred -- the old owner is still in control until the
            // Safe itself calls acceptOwnership().
            await legacyRegistry.connect(deployerSigner).transferOwnership(await safe.getAddress());
            expect(await legacyRegistry.owner()).to.equal(deployerSigner.address);
            expect(await legacyRegistry.pendingOwner()).to.equal(await safe.getAddress());

            // Step 2 (the Safe itself, requiring its own 2-of-3 threshold
            // -- proving this migration path is governed by the SAME
            // multisig property as any other Safe transaction, not a
            // special-cased single-signer exception): the Safe calls
            // acceptOwnership() on itself.
            const data = legacyRegistry.interface.encodeFunctionData("acceptOwnership", []);
            const acceptTx = await safe.createTransaction({
                transactions: [{ to: await legacyRegistry.getAddress(), value: "0", data }],
            });
            const signedByOwner0 = await safe.signTransaction(acceptTx);
            const safeAsOwner1 = await safe.connect({ signer: localTestWallet(ethers, 1).privateKey });
            const signedByBoth = await safeAsOwner1.signTransaction(signedByOwner0);

            const result = await safeAsOwner1.executeTransaction(signedByBoth);
            await result.transactionResponse.wait();

            expect(await legacyRegistry.owner()).to.equal(await safe.getAddress());
            expect(await legacyRegistry.pendingOwner()).to.equal(ethers.ZeroAddress);
        });
    });
});
