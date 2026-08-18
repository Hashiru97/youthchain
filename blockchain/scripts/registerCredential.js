import { network } from "hardhat";
import { resolveContractAddress, normalizeHash } from "./_shared.js";

const connection = await network.create();
const { ethers } = connection;

async function resolveIssuer() {
    if (process.env.ISSUER_PRIVATE_KEY) {
        return new ethers.Wallet(process.env.ISSUER_PRIVATE_KEY, ethers.provider);
    }
    // Falling back to the node's default signer is only acceptable on the
    // local, ephemeral Hardhat network — that signer is the well-known
    // public test account. Using it on any real network would mean every
    // credential is "issued" by a key anyone in the world already has
    // (S-12). Fail loudly instead of silently doing that.
    if (connection.networkName !== "localhost" && connection.networkName !== "hardhatMainnet") {
        console.error(
            `❌ ERROR: ISSUER_PRIVATE_KEY must be set when running against network "${connection.networkName}". ` +
                "Falling back to the default Hardhat signer is only safe on localhost/hardhatMainnet."
        );
        process.exit(1);
    }
    const [defaultSigner] = await ethers.getSigners();
    return defaultSigner;
}

async function main() {
    const fullHash = normalizeHash(process.env.HASH);
    console.log("➡ Using hash:", fullHash);

    const address = resolveContractAddress();
    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const issuer = await resolveIssuer();
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
