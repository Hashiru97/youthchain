import { network } from "hardhat";
import { resolveContractAddress } from "./_shared.js";

const { ethers } = await network.create();

/**
 * Lists every address that has ever been accredited or revoked, with its
 * CURRENT status. The contract's accreditedIssuers mapping isn't
 * enumerable on-chain (Solidity mappings never are), so "list all
 * issuers" isn't a single view call the way isRegistered/isValid are --
 * it has to be reconstructed from the IssuerAccredited/IssuerRevoked
 * event log, which is exactly what those events exist for. Deliberately
 * NOT mirrored into a backend DB table as the source of truth: the chain
 * already has a complete, tamper-evident history of every accreditation
 * change (same reasoning as isValid() over trusting a backend cache),
 * this script just replays it.
 */
async function main() {
    const address = resolveContractAddress();
    const Contract = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Contract.attach(address);

    const accreditedEvents = await registry.queryFilter(registry.filters.IssuerAccredited());
    const revokedEvents = await registry.queryFilter(registry.filters.IssuerRevoked());

    // Merge both event streams and keep only each address's most recent
    // change (by block number, then log index within the block) -- an
    // address accredited, then later revoked, must show as revoked, not
    // both.
    const latestByAddress = new Map();
    for (const ev of [...accreditedEvents, ...revokedEvents]) {
        const addr = ev.args.issuer;
        const isAccredited = ev.fragment.name === "IssuerAccredited";
        const existing = latestByAddress.get(addr);
        const isNewer =
            !existing ||
            ev.blockNumber > existing.blockNumber ||
            (ev.blockNumber === existing.blockNumber && ev.logIndex > existing.logIndex);
        if (isNewer) {
            latestByAddress.set(addr, { blockNumber: ev.blockNumber, logIndex: ev.logIndex, isAccredited });
        }
    }

    // Machine-parseable lines the backend greps for: one per address,
    // "ISSUER:<address>:<true|false>". Human-readable summary above it
    // for anyone running this by hand.
    for (const [addr, state] of latestByAddress.entries()) {
        console.log(`${state.isAccredited ? "✅" : "🚫"} ${addr}: ${state.isAccredited ? "accredited" : "revoked"}`);
    }
    for (const [addr, state] of latestByAddress.entries()) {
        console.log(`ISSUER:${addr}:${state.isAccredited}`);
    }

    return Array.from(latestByAddress.entries());
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
