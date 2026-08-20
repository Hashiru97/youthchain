import { network } from "hardhat";
import Safe from "@safe-global/protocol-kit";
import { resolveContractAddress } from "./_shared.js";
import { deployLocalSafeInfra, localTestWallet } from "./_safeLocalInfra.js";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const connection = await network.create();
const { ethers } = connection;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const LOCAL_NETWORKS = new Set(["localhost", "hardhatMainnet", "hardhat"]);

/**
 * For a registry that's ALREADY deployed with a single-EOA owner (every
 * deployment before this Safe integration existed) and needs migrating
 * to a Safe's governance instead. For a FRESH deployment, deploying
 * straight to a Safe via OWNER_ADDRESS (see scripts/deploy.js and
 * scripts/deploySafe.js's own printed instructions) is simpler and skips
 * this file entirely -- there's nothing to migrate away from yet.
 *
 * This performs ONLY step 1 of the two-step Ownable2Step transfer
 * (transferOwnership, signed by the CURRENT owner) on any real network --
 * step 2 (the Safe itself calling acceptOwnership()) is deliberately left
 * to the Safe's own owners, coordinated independently through the Safe UI
 * (app.safe.global) or each running their own signing step, never by
 * this script (or any other single process) holding enough of their keys
 * to do it alone -- that would defeat the entire point of moving to a
 * Safe in the first place.
 *
 * On a LOCAL/throwaway network only, this ALSO carries out step 2 for
 * real (using this project's own published local test accounts, which it
 * legitimately has enough of to meet the threshold) -- so the whole
 * migration path is provably correct end-to-end at least once, not just
 * half-built. See test/SafeOwnership.test.js for the same proof, run
 * automatically in CI rather than requiring a live local node.
 */
async function main() {
    const isLocal = LOCAL_NETWORKS.has(connection.networkName);
    const registryAddress = resolveContractAddress();

    const safeAddress = process.env.SAFE_ADDRESS || (() => {
        const safeConfigPath = path.join(__dirname, "..", "deployed-safe.json");
        if (!fs.existsSync(safeConfigPath)) return null;
        return JSON.parse(fs.readFileSync(safeConfigPath, "utf-8")).address;
    })();

    if (!safeAddress) {
        console.error(
            "❌ ERROR: no SAFE_ADDRESS env var and no deployed-safe.json found. " +
                "Deploy a Safe first (scripts/deploySafe.js), or set SAFE_ADDRESS " +
                "to an existing one."
        );
        process.exitCode = 1;
        return;
    }
    const safeCode = await ethers.provider.getCode(safeAddress);
    if (safeCode === "0x") {
        console.error(`❌ ERROR: SAFE_ADDRESS (${safeAddress}) has no contract code on "${connection.networkName}".`);
        process.exitCode = 1;
        return;
    }

    const Registry = await ethers.getContractFactory("YouthChainRegistry");
    const registry = Registry.attach(registryAddress);
    const currentOwner = await registry.owner();
    console.log(`Registry ${registryAddress} on "${connection.networkName}" — current owner: ${currentOwner}`);

    if (currentOwner.toLowerCase() === safeAddress.toLowerCase()) {
        console.log("✅ Already owned by this Safe — nothing to transfer.");
        return;
    }

    // The account signing this half MUST already be the current owner —
    // same DEPLOYER_PRIVATE_KEY convention scripts/deploy.js uses,
    // resolved into a signer by hardhat.config.js's network accounts.
    const [ownerSigner] = await ethers.getSigners();
    if (ownerSigner.address.toLowerCase() !== currentOwner.toLowerCase()) {
        console.error(
            `❌ ERROR: the configured signer (${ownerSigner.address}) is not the current ` +
                `owner (${currentOwner}) — transferOwnership would revert. Set ` +
                "DEPLOYER_PRIVATE_KEY to the current owner's key."
        );
        process.exitCode = 1;
        return;
    }

    console.log(`Step 1/2: transferOwnership(${safeAddress}) from the current owner...`);
    const transferTx = await registry.connect(ownerSigner).transferOwnership(safeAddress);
    await transferTx.wait();
    console.log("✅ Step 1 done — pendingOwner is now the Safe. Ownership has NOT changed yet.");
    console.log("   registry.owner() is still:", await registry.owner());
    console.log("   registry.pendingOwner():", await registry.pendingOwner());

    if (!isLocal) {
        console.log(
            "\nStep 2/2 is NOT performed by this script on a real network — that's " +
                "the whole point of moving to a Safe. The Safe's own owners must now " +
                `call acceptOwnership() on ${registryAddress} THROUGH THE SAFE ` +
                `(${safeAddress}), meeting its threshold, via app.safe.global or their ` +
                "own independent signing tooling. Until they do, the CURRENT owner " +
                "above remains in control (Ownable2Step's whole safety property: a " +
                "botched transfer target never locks anyone out)."
        );
        return;
    }

    console.log(
        "\nLocal network — carrying out step 2 for real, using this project's own " +
            "published local test accounts (owners 0 and 1 of the demo Safe; see " +
            "_safeLocalInfra.js) to meet the threshold, so this migration path is " +
            "proven correct end-to-end, not just half-built. On a real Safe, this " +
            "confirm+execute step happens across separate humans, never one process " +
            "holding multiple keys — see this file's own docstring above."
    );

    // protocol-kit needs to know a deployed MultiSend/fallback-handler
    // etc address to build/execute a transaction THROUGH the existing
    // Safe above -- those are stateless, network-wide utility contracts
    // any Safe on this chain can use, not something tied to which
    // specific run originally deployed this one Safe. This process has
    // no persistent memory of what scripts/deploySafe.js's earlier,
    // separate run used, so a fresh set is deployed here instead;
    // verified for real (see this file's own test coverage in
    // test/SafeOwnership.test.js, and this exact script run end-to-end
    // against a live local node) that this works against a Safe deployed
    // by an entirely separate prior script invocation, not just within
    // one script's own single run.
    const [deployerSigner] = await ethers.getSigners();
    const contractNetworks = await deployLocalSafeInfra(ethers, deployerSigner);

    const protocolKit = await Safe.init({
        provider: connection.provider,
        signer: localTestWallet(ethers, 0).privateKey,
        safeAddress,
        contractNetworks,
    });

    const data = registry.interface.encodeFunctionData("acceptOwnership", []);
    const acceptTx = await protocolKit.createTransaction({
        transactions: [{ to: registryAddress, value: "0", data }],
    });
    const signedByOwner0 = await protocolKit.signTransaction(acceptTx);
    const asOwner1 = await protocolKit.connect({ signer: localTestWallet(ethers, 1).privateKey });
    const signedByBoth = await asOwner1.signTransaction(signedByOwner0);

    const result = await asOwner1.executeTransaction(signedByBoth);
    await result.transactionResponse.wait();

    console.log("✅ Step 2 done — acceptOwnership() executed through the Safe.");
    console.log("   registry.owner() is now:", await registry.owner());
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
