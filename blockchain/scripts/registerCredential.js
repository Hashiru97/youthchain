import { network } from "hardhat";
import { resolveContractAddress, normalizeHash, resolveIssuerSigner } from "./_shared.js";

const connection = await network.create();
const { ethers } = connection;

async function main() {
    const fullHash = normalizeHash(process.env.HASH);
    console.log("➡ Using hash:", fullHash);

    const address = resolveContractAddress();
    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const issuer = await resolveIssuerSigner(ethers, connection);
    console.log("➡ Using issuer:", issuer.address);
    console.log("➡ Using contract:", address);

    const tx = await registry.connect(issuer).registerCredential(fullHash);
    console.log("📤 Sent tx:", tx.hash);

    const receipt = await tx.wait();
    console.log("✅ Mined tx:", receipt.hash);
    // ASCII-only, unambiguous marker line for the backend to grep — the
    // emoji-prefixed line above is for a human reading the console, but
    // relying on it as the actual parsing contract turned out to be fragile
    // in practice: Windows' console encoding can mangle the emoji in a
    // piped/redirected subprocess, silently breaking the match (this was a
    // real, observed bug, not a hypothetical one — see MINED_TX below).
    console.log("MINED_TX:" + receipt.hash);

    return receipt.hash;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
