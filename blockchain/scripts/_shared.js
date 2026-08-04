import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Resolves the deployed contract address: an explicit CONTRACT_ADDRESS env
 * var wins, otherwise falls back to whatever deploy.js last wrote to
 * deployed.json. Shared by registerCredential.js and checkRegistered.js so
 * both the write and read paths stay consistent (BL-27).
 */
export function resolveContractAddress() {
    if (process.env.CONTRACT_ADDRESS) {
        return process.env.CONTRACT_ADDRESS;
    }
    const deployedPath = path.join(__dirname, "..", "deployed.json");
    if (!fs.existsSync(deployedPath)) {
        console.error(
            "❌ ERROR: no CONTRACT_ADDRESS env var and no deployed.json found. " +
                "Run scripts/deploy.js first, or set CONTRACT_ADDRESS."
        );
        process.exit(1);
    }
    const deployed = JSON.parse(fs.readFileSync(deployedPath, "utf-8"));
    return deployed.address;
}

export function normalizeHash(hash) {
    if (!hash) {
        console.error("❌ ERROR: HASH environment variable not set");
        process.exit(1);
    }
    const full = hash.startsWith("0x") ? hash : "0x" + hash;
    if (!/^0x[0-9a-fA-F]{64}$/.test(full)) {
        console.error(`❌ ERROR: HASH is not a valid 32-byte hex value: ${full}`);
        process.exit(1);
    }
    return full;
}
