import { expect } from "chai";
import hre from "hardhat";
import { anyValue } from "@nomicfoundation/hardhat-chai-matchers/withArgs.js";

const { ethers } = hre;

describe("YouthChainRegistry", function () {
  let registry;
  let owner, issuer, stranger;

  const HASH_A = "0x" + "11".repeat(32);
  const HASH_B = "0x" + "22".repeat(32);
  const ZERO_HASH = "0x" + "00".repeat(32);

  beforeEach(async function () {
    [owner, issuer, stranger] = await ethers.getSigners();
    const Registry = await ethers.getContractFactory("YouthChainRegistry");
    registry = await Registry.deploy(owner.address);
    await registry.waitForDeployment();
  });

  it("auto-accredits the initial owner as an issuer", async function () {
    expect(await registry.accreditedIssuers(owner.address)).to.equal(true);
  });

  it("rejects registerCredential from a non-accredited address", async function () {
    await expect(
      registry.connect(stranger).registerCredential(HASH_A)
    ).to.be.revertedWith("not an accredited issuer");
  });

  it("allows an accredited issuer to register a credential and emits the event", async function () {
    await expect(registry.connect(owner).registerCredential(HASH_A))
      .to.emit(registry, "CredentialRegistered")
      .withArgs(HASH_A, owner.address, anyValue);

    expect(await registry.isRegistered(HASH_A)).to.equal(true);
  });

  it("rejects a zero hash", async function () {
    await expect(
      registry.connect(owner).registerCredential(ZERO_HASH)
    ).to.be.revertedWith("empty hash");
  });

  it("rejects registering the same hash twice", async function () {
    await registry.connect(owner).registerCredential(HASH_A);
    await expect(
      registry.connect(owner).registerCredential(HASH_A)
    ).to.be.revertedWith("already exists");
  });

  it("isRegistered returns false for an unknown hash", async function () {
    expect(await registry.isRegistered(HASH_B)).to.equal(false);
  });

  it("lets the owner accredit a new issuer, who can then register credentials", async function () {
    await expect(
      registry.connect(issuer).registerCredential(HASH_A)
    ).to.be.revertedWith("not an accredited issuer");

    await expect(registry.connect(owner).accreditIssuer(issuer.address))
      .to.emit(registry, "IssuerAccredited")
      .withArgs(issuer.address);

    await registry.connect(issuer).registerCredential(HASH_A);
    expect(await registry.isRegistered(HASH_A)).to.equal(true);
  });

  it("rejects accreditIssuer/revokeIssuer from a non-owner", async function () {
    await expect(
      registry.connect(stranger).accreditIssuer(stranger.address)
    ).to.be.reverted;
    await expect(
      registry.connect(stranger).revokeIssuer(owner.address)
    ).to.be.reverted;
  });

  it("lets the owner revoke a previously-accredited issuer", async function () {
    await registry.connect(owner).accreditIssuer(issuer.address);
    await registry.connect(owner).revokeIssuer(issuer.address);

    await expect(
      registry.connect(issuer).registerCredential(HASH_A)
    ).to.be.revertedWith("not an accredited issuer");
  });
});
