import { network } from "hardhat";
import { resolveContractAddress, normalizeAddress } from "./_shared.js";

const connection = await network.create();
const { ethers } = connection;

/**
 * revokeIssuer() is onlyOwner (see YouthChainRegistry.sol) -- same
 * reasoning as accreditIssuer.js/revokeCredential.js: needs
 * DEPLOYER_PRIVATE_KEY, not ISSUER_PRIVATE_KEY.
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
    console.log("➡ Revoking address:", issuerAddress);

    const address = resolveContractAddress();
    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const owner = await resolveOwner();
    console.log("➡ Using owner:", owner.address);
    console.log("➡ Using contract:", address);

    const tx = await registry.connect(owner).revokeIssuer(issuerAddress);
    console.log("📤 Sent tx:", tx.hash);

    const receipt = await tx.wait();
    console.log("✅ Mined tx:", receipt.hash);
    console.log("MINED_TX:" + receipt.hash);

    return receipt.hash;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
