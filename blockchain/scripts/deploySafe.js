import { network } from "hardhat";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import Safe from "@safe-global/protocol-kit";
import { deployLocalSafeInfra, localTestWallet } from "./_safeLocalInfra.js";

const connection = await network.create();
const { ethers } = connection;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const configPath = path.join(__dirname, "..", "deployed-safe.json");

// Real gap this whole file exists to close (see the engineering audit):
// YouthChainRegistry.sol's governance (accreditIssuer/revokeIssuer/
// revokeCredential/transferOwnership) is currently controlled by a single
// EOA private key, co-located on the same host as the backend server --
// one compromise of that host takes the entire registry's governance.
// Making the registry owned by a Safe (a real, audited multisig contract)
// instead means no single compromised machine/key is enough on its own.
//
// Local/dev networks only, by default: uses 3 of Hardhat's own published
// test accounts as demo owners (see _safeLocalInfra.js's own comment on
// exactly why these specific keys, not just addresses, are needed here).
// SAFE_OWNERS/SAFE_THRESHOLD override this for anything else -- and are
// REQUIRED (this script refuses without them) once the target network
// isn't a local one, since deploying a Safe "owned" by this script's own
// demo accounts anywhere reachable by anyone but you would recreate the
// exact single-point-of-trust problem this exists to fix, just wearing a
// multisig's UI.
const LOCAL_NETWORKS = new Set(["localhost", "hardhatMainnet", "hardhat"]);

function parseOwners(envValue) {
    if (!envValue) return null;
    const owners = envValue.split(",").map((s) => s.trim()).filter(Boolean);
    for (const owner of owners) {
        if (!/^0x[0-9a-fA-F]{40}$/.test(owner)) {
            throw new Error(`SAFE_OWNERS contains an invalid address: ${owner}`);
        }
    }
    return owners;
}

async function main() {
    const isLocal = LOCAL_NETWORKS.has(connection.networkName);
    const envOwners = parseOwners(process.env.SAFE_OWNERS);
    const threshold = parseInt(process.env.SAFE_THRESHOLD || "2", 10);

    if (!isLocal && !envOwners) {
        console.error(
            "❌ ERROR: SAFE_OWNERS is required on any network other than " +
            [...LOCAL_NETWORKS].join("/") + ". Refusing to deploy a Safe " +
            "owned by this script's own local demo accounts anywhere " +
            "reachable by anyone but you.\n\n" +
            "Set SAFE_OWNERS to a comma-separated list of the REAL " +
            "signers' addresses -- their private keys stay with them; " +
            "this script never needs or asks for those, only the public " +
            "addresses. Set SAFE_THRESHOLD too (default 2) if it should " +
            "differ from 2-of-N."
        );
        process.exitCode = 1;
        return;
    }

    // On a real network, deploying signer = DEPLOYER_PRIVATE_KEY, same
    // account deploy.js already uses -- this only PAYS to deploy the Safe
    // proxy (a few cents of gas), it is never one of the Safe's owners
    // unless its address also appears in SAFE_OWNERS.
    const [deployerRpcSigner] = await ethers.getSigners();
    const owners = envOwners || [
        localTestWallet(ethers, 0).address,
        localTestWallet(ethers, 1).address,
        localTestWallet(ethers, 2).address,
    ];
    // protocol-kit needs a raw private-key signer (for EIP-712 signing of
    // the deployment), not a JSON-RPC-managed one -- on a local network
    // that's the same well-known test mnemonic as above; on a real
    // network it's DEPLOYER_PRIVATE_KEY itself, exactly like deploy.js.
    const signerPrivateKey = isLocal
        ? localTestWallet(ethers, 0).privateKey
        : process.env.DEPLOYER_PRIVATE_KEY;

    if (owners.length < threshold) {
        console.error(`❌ ERROR: threshold (${threshold}) exceeds owner count (${owners.length}).`);
        process.exitCode = 1;
        return;
    }

    console.log(`Deploying a ${threshold}-of-${owners.length} Safe on "${connection.networkName}"...`);
    console.log("Owners:", owners);
    if (isLocal && !envOwners) {
        console.log(
            "⚠️  Using Hardhat's own well-known local test accounts as " +
            "demo owners -- safe ONLY on this local/throwaway chain. Set " +
            "SAFE_OWNERS explicitly for anything else, including a public " +
            "testnet."
        );
    }

    let contractNetworks;
    if (isLocal) {
        console.log(
            "Local network detected -- deploying the real Safe v1.4.1 core " +
            "contracts fresh (no canonical deployment exists on a throwaway " +
            "chain; see _safeLocalInfra.js)..."
        );
        contractNetworks = await deployLocalSafeInfra(ethers, deployerRpcSigner);
    }
    // On a real network, contractNetworks stays undefined -- protocol-kit
    // resolves the real, canonical Safe contract addresses for that chain
    // itself via @safe-global/safe-deployments.

    const protocolKit = await Safe.init({
        provider: connection.provider,
        signer: signerPrivateKey,
        predictedSafe: {
            safeAccountConfig: { owners, threshold },
            safeDeploymentConfig: { safeVersion: "1.4.1" },
        },
        contractNetworks,
    });

    const predictedAddress = await protocolKit.getAddress();
    console.log("Predicted Safe address:", predictedAddress);

    const deploymentTransaction = await protocolKit.createSafeDeploymentTransaction();
    // deployerRpcSigner, not a raw ethers.Wallet -- Hardhat's own signer
    // is already correctly wired to `ethers.provider` regardless of
    // network, and (on the local networks this branch matters for) its
    // address is identical to localTestWallet(ethers, 0)'s anyway, since
    // both derive from the same standard mnemonic. The raw private key
    // above exists only for protocol-kit's OWN internal EIP-712 signing,
    // a separate concern from who broadcasts this plain transaction.
    const txResponse = await deployerRpcSigner.sendTransaction({
        to: deploymentTransaction.to,
        value: deploymentTransaction.value,
        data: deploymentTransaction.data,
    });
    await txResponse.wait();

    const deployedSafe = await protocolKit.connect({ safeAddress: predictedAddress });
    const isDeployed = await deployedSafe.isSafeDeployed();
    if (!isDeployed) {
        console.error("❌ ERROR: Safe deployment transaction succeeded but isSafeDeployed() is still false.");
        process.exitCode = 1;
        return;
    }

    console.log("✅ Safe deployed to:", predictedAddress);
    console.log("Owners on-chain:", await deployedSafe.getOwners());
    console.log("Threshold on-chain:", await deployedSafe.getThreshold());

    fs.writeFileSync(
        configPath,
        JSON.stringify(
            {
                address: predictedAddress,
                network: connection.networkName,
                owners,
                threshold,
            },
            null,
            2
        )
    );
    console.log(`Wrote ${configPath}`);
    console.log(
        "\nNext step: point YouthChainRegistry's ownership at this Safe. " +
        "For a FRESH registry deployment, the cleanest path is deploying " +
        "straight to it:\n" +
        `  OWNER_ADDRESS=${predictedAddress} OWNER_ADDRESS_CONFIRMED=1 \\\n` +
        "  npx hardhat run scripts/deploy.js --network " + connection.networkName + "\n" +
        "For an ALREADY-deployed registry, see " +
        "scripts/transferRegistryOwnershipToSafe.js instead."
    );
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
