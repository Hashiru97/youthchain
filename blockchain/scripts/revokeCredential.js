import { network } from "hardhat";
import { resolveContractAddress, normalizeHash, resolveIssuerAddress } from "./_shared.js";

const connection = await network.create();
const { ethers } = connection;

/**
 * revokeCredential() is onlyOwner (see YouthChainRegistry.sol) -- deliberately
 * NOT callable with ISSUER_PRIVATE_KEY the way registerCredential.js is,
 * since an issuer should not be able to unilaterally erase evidence of
 * their own mistake or misconduct. Needs the contract OWNER's key
 * (DEPLOYER_PRIVATE_KEY, per .env.example -- "the account that deploys the
 * contract and becomes its owner"). Separately, WHOSE registration of this
 * hash is being revoked is resolved via resolveIssuerAddress (see
 * checkRegistered.js's own comment) -- front-running fix, now that more
 * than one issuer can hold a registration for the same hash, the owner
 * must specify exactly which one.
 */
async function resolveOwner() {
    if (process.env.DEPLOYER_PRIVATE_KEY) {
        return new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, ethers.provider);
    }
    // Same reasoning as registerCredential.js's resolveIssuer(): falling
    // back to the node's well-known default signer is only safe on a
    // local, non-publicly-reachable network.
    if (connection.networkName !== "localhost" && connection.networkName !== "hardhatMainnet") {
        console.error(
            `❌ ERROR: DEPLOYER_PRIVATE_KEY must be set when running against network "${connection.networkName}". ` +
                "Falling back to the default Hardhat signer is only safe on localhost/hardhatMainnet."
        );
        process.exit(1);
    }
    const [defaultSigner] = await ethers.getSigners();
    return defaultSigner;
}

async function main() {
    const fullHash = normalizeHash(process.env.HASH);
    console.log("➡ Revoking hash:", fullHash);

    const address = resolveContractAddress();
    const issuerAddress = await resolveIssuerAddress(ethers, connection);
    console.log("➡ Revoking registration by issuer:", issuerAddress);

    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const owner = await resolveOwner();
    console.log("➡ Using owner:", owner.address);
    console.log("➡ Using contract:", address);

    const tx = await registry.connect(owner).revokeCredential(issuerAddress, fullHash);
    console.log("📤 Sent tx:", tx.hash);

    const receipt = await tx.wait();
    console.log("✅ Mined tx:", receipt.hash);
    // Machine-parseable marker, mirroring registerCredential.js's
    // MINED_TX: convention (see that script's comment for why an
    // ASCII-only line matters on Windows).
    console.log("MINED_TX:" + receipt.hash);

    return receipt.hash;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
