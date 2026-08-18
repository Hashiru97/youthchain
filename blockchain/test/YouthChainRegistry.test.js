import { expect } from "chai";
import { network } from "hardhat";
import { anyValue } from "@nomicfoundation/hardhat-ethers-chai-matchers/withArgs";

const { ethers } = await network.create();

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
    ).to.be.revert(ethers);
    await expect(
      registry.connect(stranger).revokeIssuer(owner.address)
    ).to.be.revert(ethers);
  });

  it("lets the owner revoke a previously-accredited issuer", async function () {
    await registry.connect(owner).accreditIssuer(issuer.address);
    await registry.connect(owner).revokeIssuer(issuer.address);

    await expect(
      registry.connect(issuer).registerCredential(HASH_A)
    ).to.be.revertedWith("not an accredited issuer");
  });

  it("emits IssuerRevoked with the correct address", async function () {
    await registry.connect(owner).accreditIssuer(issuer.address);
    await expect(registry.connect(owner).revokeIssuer(issuer.address))
      .to.emit(registry, "IssuerRevoked")
      .withArgs(issuer.address);
  });

  it("does not retroactively invalidate a credential registered before its issuer was revoked", async function () {
    await registry.connect(owner).accreditIssuer(issuer.address);
    await registry.connect(issuer).registerCredential(HASH_A);
    await registry.connect(owner).revokeIssuer(issuer.address);

    expect(await registry.isRegistered(HASH_A)).to.equal(true);
    const stored = await registry.credentials(HASH_A);
    expect(stored.issuer).to.equal(issuer.address);
  });

  // --- Ownership governance (Ownable2Step + disabled renounceOwnership) ---
  // Real gap found via a full-codebase review: plain Ownable's
  // transferOwnership() moves control in one transaction with no
  // confirmation, and renounceOwnership() could permanently and
  // irrecoverably disable issuer governance -- neither had any test
  // coverage at all before this.

  it("renounceOwnership always reverts, even for the real owner", async function () {
    await expect(registry.connect(owner).renounceOwnership()).to.be.revertedWith(
      "YouthChainRegistry: ownership renouncement is disabled"
    );
    expect(await registry.owner()).to.equal(owner.address);
  });

  it("renounceOwnership reverts for a non-owner too (onlyOwner still applies)", async function () {
    await expect(registry.connect(stranger).renounceOwnership()).to.be.revert(ethers);
  });

  it("transferOwnership is two-step: ownership does not change until the new owner accepts", async function () {
    await expect(registry.connect(owner).transferOwnership(issuer.address))
      .to.emit(registry, "OwnershipTransferStarted")
      .withArgs(owner.address, issuer.address);

    // Not yet transferred -- the old owner can still act, the pending
    // owner cannot, until acceptOwnership() is called.
    expect(await registry.owner()).to.equal(owner.address);
    expect(await registry.pendingOwner()).to.equal(issuer.address);
    await registry.connect(owner).accreditIssuer(stranger.address); // old owner still in control

    await registry.connect(issuer).acceptOwnership();

    expect(await registry.owner()).to.equal(issuer.address);
    // Old owner has lost control now that the transfer completed.
    await expect(
      registry.connect(owner).accreditIssuer(stranger.address)
    ).to.be.revert(ethers);
  });

  it("acceptOwnership reverts when called by anyone other than the pending owner", async function () {
    await registry.connect(owner).transferOwnership(issuer.address);
    await expect(registry.connect(stranger).acceptOwnership()).to.be.revert(ethers);
    expect(await registry.owner()).to.equal(owner.address);
  });

  // --- Credential revocation ---
  // Real gap found via a full-codebase review: this registry had no way
  // to invalidate a fraudulent/erroneous credential once written.

  it("isValid is true for a freshly registered credential", async function () {
    await registry.connect(owner).registerCredential(HASH_A);
    expect(await registry.isValid(HASH_A)).to.equal(true);
  });

  it("isValid is false for a hash that was never registered", async function () {
    expect(await registry.isValid(HASH_B)).to.equal(false);
  });

  it("lets the owner revoke a registered credential, emitting CredentialRevoked", async function () {
    await registry.connect(owner).registerCredential(HASH_A);

    await expect(registry.connect(owner).revokeCredential(HASH_A))
      .to.emit(registry, "CredentialRevoked")
      .withArgs(HASH_A, owner.address, anyValue);

    expect(await registry.revokedCredentials(HASH_A)).to.equal(true);
  });

  it("isRegistered stays true after revocation, but isValid becomes false", async function () {
    await registry.connect(owner).registerCredential(HASH_A);
    await registry.connect(owner).revokeCredential(HASH_A);

    // The original registration is a permanent, auditable fact --
    // revoking doesn't erase that it was once issued, only that it
    // should still be trusted.
    expect(await registry.isRegistered(HASH_A)).to.equal(true);
    expect(await registry.isValid(HASH_A)).to.equal(false);
    const stored = await registry.credentials(HASH_A);
    expect(stored.issuer).to.equal(owner.address); // record itself is untouched
  });

  it("rejects revokeCredential from a non-owner", async function () {
    await registry.connect(owner).registerCredential(HASH_A);
    await expect(
      registry.connect(stranger).revokeCredential(HASH_A)
    ).to.be.revert(ethers);
    expect(await registry.isValid(HASH_A)).to.equal(true); // unaffected
  });

  it("rejects revoking a hash that was never registered", async function () {
    await expect(
      registry.connect(owner).revokeCredential(HASH_B)
    ).to.be.revertedWith("credential does not exist");
  });

  it("rejects revoking the same credential twice", async function () {
    await registry.connect(owner).registerCredential(HASH_A);
    await registry.connect(owner).revokeCredential(HASH_A);
    await expect(
      registry.connect(owner).revokeCredential(HASH_A)
    ).to.be.revertedWith("already revoked");
  });

  it("an issuer (not the owner) cannot revoke their own credential", async function () {
    await registry.connect(owner).accreditIssuer(issuer.address);
    await registry.connect(issuer).registerCredential(HASH_A);

    await expect(
      registry.connect(issuer).revokeCredential(HASH_A)
    ).to.be.revert(ethers);
    expect(await registry.isValid(HASH_A)).to.equal(true);
  });
});
