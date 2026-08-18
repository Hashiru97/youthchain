import { network } from "hardhat";
import { resolveContractAddress, normalizeHash } from "./_shared.js";

const { ethers } = await network.create();

/**
 * Read-only counterpart to revokeCredential.js, and the check a verifier
 * should actually rely on: registered AND not revoked. Deliberately a
 * separate script from checkRegistered.js (isRegistered) rather than
 * changing that one's underlying call -- _write_onchain_tx_for_credential
 * in app.py calls checkRegistered.js to decide whether a write would be a
 * guaranteed-revert duplicate, and that check must stay existence-only
 * (a revoked hash still "exists" and must not be re-registered); only the
 * user-facing /verify and /employer/verify display should treat a revoked
 * credential as no longer valid.
 */
async function main() {
    const fullHash = normalizeHash(process.env.HASH);
    const address = resolveContractAddress();

    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const valid = await registry.isValid(fullHash);
    // Machine-parseable line the backend greps for, mirroring
    // checkRegistered.js's "REGISTERED:" convention.
    console.log("VALID:" + valid);
    return valid;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
