import { expect } from "chai";
import { ownerAddressSafetyError } from "../scripts/_shared.js";

/**
 * Unit coverage for deploy.js's OWNER_ADDRESS safety check (see its own
 * comment and _shared.js's ownerAddressSafetyError docstring for the
 * full reasoning) -- pure logic, no live network needed. Also manually
 * verified end-to-end against a real local Hardhat node while building
 * this (a plain EOA address refuses, a real deployed Safe address
 * refuses without OWNER_ADDRESS_CONFIRMED, and succeeds with it) -- this
 * test file is what keeps that behavior locked in going forward.
 */
describe("deploy.js OWNER_ADDRESS safety check", function () {
    it("refuses a plain EOA address (no contract code), regardless of confirmation", function () {
        const error = ownerAddressSafetyError({
            ownerAddress: "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            networkName: "sepolia",
            hasCode: false,
            isConfirmed: true,
        });
        expect(error).to.be.a("string");
        expect(error).to.include("has no contract code");
    });

    it("refuses a deployed contract address without OWNER_ADDRESS_CONFIRMED", function () {
        const error = ownerAddressSafetyError({
            ownerAddress: "0xAe9AbA1d572c3718903cB04f951EEe1e55173222",
            networkName: "sepolia",
            hasCode: true,
            isConfirmed: false,
        });
        expect(error).to.be.a("string");
        expect(error).to.include("no two-step confirmation");
    });

    it("proceeds for a deployed contract address WITH OWNER_ADDRESS_CONFIRMED", function () {
        const error = ownerAddressSafetyError({
            ownerAddress: "0xAe9AbA1d572c3718903cB04f951EEe1e55173222",
            networkName: "sepolia",
            hasCode: true,
            isConfirmed: true,
        });
        expect(error).to.equal(null);
    });
});
