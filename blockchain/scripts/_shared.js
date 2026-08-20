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

/**
 * Validates the ADDRESS env var used by accreditIssuer.js/revokeIssuer.js/
 * listIssuers.js. Deliberately a plain regex check (20-byte hex), not
 * ethers.isAddress()'s checksum validation -- this only needs to catch a
 * malformed value before it reaches the contract call (which would revert
 * anyway on a genuinely invalid address), not enforce EIP-55 casing on
 * what's typically pasted straight from a wallet UI or block explorer.
 */
/**
 * Pure decision logic for deploy.js's OWNER_ADDRESS safety check (see its
 * own comment for the full reasoning: unlike a later transferOwnership()
 * call, this constructor argument becomes the PERMANENT owner the
 * instant deployment mines, with no two-step confirmation of its own).
 * Deliberately separate from the actual `ethers.provider.getCode()` I/O
 * call deploy.js makes -- this only takes the two already-resolved
 * booleans, so it's directly unit-testable without needing a live
 * network (see test/DeployOwnerAddressSafety.test.js).
 *
 * Returns an error message string if deployment should be refused, or
 * null if it's safe to proceed.
 */
export function ownerAddressSafetyError({ ownerAddress, networkName, hasCode, isConfirmed }) {
    if (!hasCode) {
        return (
            `OWNER_ADDRESS (${ownerAddress}) has no contract code on "${networkName}". ` +
            "A Safe (or any other multisig/contract owner) must already be deployed on " +
            "THIS network before pointing the registry at it -- see scripts/deploySafe.js. " +
            "If this is meant to be a plain EOA (not recommended for anything beyond " +
            "local/dev use -- see the engineering audit's own reasoning on single-key " +
            "governance), that choice still needs the explicit confirmation below; this " +
            "check cannot distinguish 'real EOA, chosen on purpose' from 'typo', so it " +
            "refuses either way without it."
        );
    }
    if (!isConfirmed) {
        return (
            `about to deploy YouthChainRegistry on "${networkName}" with owner ${ownerAddress} ` +
            "PERMANENTLY, effective immediately, with no two-step confirmation and no way to " +
            "undo a wrong address afterward. Verify that address on a block explorer (or " +
            "against deployed-safe.json if it's a Safe deployed by this project's own " +
            "scripts/deploySafe.js) first, then set OWNER_ADDRESS_CONFIRMED=1 to proceed."
        );
    }
    return null;
}

export function normalizeAddress(address) {
    if (!address) {
        console.error("❌ ERROR: ADDRESS environment variable not set");
        process.exit(1);
    }
    if (!/^0x[0-9a-fA-F]{40}$/.test(address)) {
        console.error(`❌ ERROR: ADDRESS is not a valid 20-byte hex address: ${address}`);
        process.exit(1);
    }
    return address;
}
