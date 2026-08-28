import { network } from "hardhat";
import { resolveContractAddress, normalizeHash, resolveIssuerAddress } from "./_shared.js";

const connection = await network.create();
const { ethers } = connection;

/**
 * Read-only counterpart to registerCredential.js: calls the contract's own
 * isRegistered(issuer, hash) view function and prints the result.
 *
 * Closes the central finding of Phase 6 of the engineering review: every
 * "verification" in the product previously only ever queried the backend's
 * own SQLite database, never the chain itself — meaning "blockchain
 * verified" was really just "the backend's database says so", which
 * defeats a primary reason to use a blockchain (removing the need to trust
 * a single central party). This script is what the backend now calls from
 * /verify and /employer/verify to actually check the chain.
 *
 * Takes an issuer address now (front-running fix -- see
 * YouthChainRegistry.sol's own docstring on credentialKey): an optional
 * ADDRESS env var, defaulting to this backend's own issuer address (see
 * resolveIssuerAddress in _shared.js) when not set, which is correct for
 * the overwhelmingly common case of checking a credential this backend
 * itself registered.
 */
async function main() {
    const fullHash = normalizeHash(process.env.HASH);
    const address = resolveContractAddress();
    const issuerAddress = await resolveIssuerAddress(ethers, connection);

    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const registered = await registry.isRegistered(issuerAddress, fullHash);
    // Machine-parseable line the backend greps for, mirroring the existing
    // "✅ Mined tx:" convention used by registerCredential.js.
    console.log("REGISTERED:" + registered);
    return registered;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
