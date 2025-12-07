import pkg from "hardhat";
const { ethers } = pkg;

async function main() {
    const hash = process.env.HASH;

    if (!hash) {
        console.error("❌ ERROR: HASH environment variable not set");
        process.exit(1);
    }

    // Normalize into 0x-prefixed bytes32
    const fullHash = hash.startsWith("0x") ? hash : "0x" + hash;

    console.log("➡ Using hash:", fullHash);

    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach("0x5FbDB2315678afecb367f032d93F642f64180aa3");

    const [issuer] = await ethers.getSigners();

    console.log("➡ Using issuer:", issuer.address);

    const tx = await registry.registerCredential(fullHash);
    console.log("📤 Sent tx:", tx.hash);

    const receipt = await tx.wait();
    console.log("✅ Mined tx:", receipt.hash);

    return receipt.hash;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
