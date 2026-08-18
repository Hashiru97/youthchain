import { network } from "hardhat";
import { resolveContractAddress, normalizeAddress } from "./_shared.js";

const { ethers } = await network.create();

/**
 * Read-only check of the contract's own accreditedIssuers(address) public
 * mapping getter -- same reasoning as checkRegistered.js/checkValid.js:
 * the chain is the source of truth for who can call registerCredential(),
 * not any backend-side record of it.
 */
async function main() {
    const issuerAddress = normalizeAddress(process.env.ADDRESS);
    const address = resolveContractAddress();

    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const accredited = await registry.accreditedIssuers(issuerAddress);
    // Machine-parseable line the backend greps for, mirroring
    // checkRegistered.js's "REGISTERED:" convention.
    console.log("ACCREDITED:" + accredited);
    return accredited;
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
