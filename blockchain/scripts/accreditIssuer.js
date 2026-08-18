import { network } from "hardhat";
import { resolveContractAddress, normalizeAddress } from "./_shared.js";

const connection = await network.create();
const { ethers } = connection;

/**
 * accreditIssuer() is onlyOwner (see YouthChainRegistry.sol) -- same
 * reasoning as revokeCredential.js: only the contract's owner can grant
 * issuer status, so this needs DEPLOYER_PRIVATE_KEY, not
 * ISSUER_PRIVATE_KEY.
 */
async function resolveOwner() {
    if (process.env.DEPLOYER_PRIVATE_KEY) {
        return new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, ethers.provider);
    }
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
    const issuerAddress = normalizeAddress(process.env.ADDRESS);
    console.log("➡ Accrediting address:", issuerAddress);

    const address = resolveContractAddress();
    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const owner = await resolveOwner();
    console.log("➡ Using owner:", owner.address);
    console.log("➡ Using contract:", address);

    const tx = await registry.connect(owner).accreditIssuer(issuerAddress);
    console.log("📤 Sent tx:", tx.hash);

    const receipt = await tx.wait();
    console.log("✅ Mined tx:", receipt.hash);
    // ASCII-only marker, mirroring registerCredential.js/revokeCredential.js's
    // MINED_TX: convention -- see that script's comment for why an
    // emoji-prefixed line alone isn't safe to parse on Windows.
    console.log("MINED_TX:" + receipt.hash);

    return receipt.hash;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
