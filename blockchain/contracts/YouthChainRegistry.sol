// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import "@openzeppelin/contracts/access/Ownable2Step.sol";

/// @title YouthChainRegistry
/// @notice Minimal on-chain credential registry used by the YouthChain platform.
///         Each credential is represented by a bytes32 hash (e.g. SHA-256 of the file).
/// @dev Access control added per engineering review finding S-11: the original
///      version let ANY address register a credential and be recorded as its
///      issuer. That was only safe by accident (the local Hardhat network isn't
///      publicly reachable) and would become a critical vulnerability the
///      moment this contract is deployed anywhere real. Only addresses the
///      contract owner has explicitly accredited may call registerCredential.
/// @dev Ownable2Step (not plain Ownable) per a later full-codebase review:
///      plain Ownable's transferOwnership() moves ownership in one
///      transaction with no confirmation from the receiving address, so a
///      single mistyped/unreachable address permanently and irrecoverably
///      loses issuer-governance control (no one could ever accredit or
///      revoke an issuer again) -- a real, previously-undiscovered risk for
///      a contract meant to be national infrastructure. Ownable2Step
///      requires the new owner to call acceptOwnership() before the
///      transfer takes effect, catching exactly that mistake before it's
///      irreversible. renounceOwnership() is separately overridden below
///      to always revert for the same reason -- there's no legitimate
///      product reason for this contract's governance to ever be able to
///      disappear entirely, and nothing in this codebase calls it.
contract YouthChainRegistry is Ownable2Step {
    struct Credential {
        bytes32 credentialHash;
        address issuer;
        uint256 issuedAt;
    }

    // Keyed by credentialKey(issuer, credentialHash), NOT credentialHash
    // alone -- real front-running vulnerability found via a full security
    // review and closed here: registerCredential() used to key this map
    // by hash only, and permanently blocked any future registration of
    // that same hash the instant the first transaction mined ("already
    // exists" forever after, with no way to fix a misattributed record).
    // Since accreditIssuer() already supports multiple, mutually
    // independent accredited issuers, a second accredited issuer watching
    // the public mempool could front-run a real issuer's own
    // registerCredential(hash) transaction with higher gas, permanently
    // stealing that document's registration and locking the true issuer
    // out of ever registering it. Keying by (issuer, hash) instead means
    // each issuer's registration of a given document lives in its own
    // slot -- a verifier now confirms "did THIS specific issuer register
    // this exact document" rather than the ambiguous, hijackable "has
    // this hash been registered by *someone*", which is arguably the more
    // honest claim this platform was always trying to make anyway (see
    // isValid's own docstring).
    mapping(bytes32 => Credential) public credentials;
    mapping(address => bool) public accreditedIssuers;
    // Real gap found via a full-codebase review: this registry had no way
    // to invalidate a credential once written -- for a platform whose
    // stated mission includes "solving fake credentials," a fraudulent or
    // erroneously-issued credential stayed permanently "valid" forever
    // with no recourse. A separate mapping (not deleting/mutating the
    // Credential struct itself) so the original registration -- who
    // issued it and when -- remains a permanent, auditable fact even
    // after revocation; only its current trust status changes. Keyed the
    // same composite way as `credentials` above -- keeping this one
    // hash-only after fixing `credentials` would have reopened an
    // equivalent bug in the other direction: revoking one issuer's
    // credential would have also silently revoked a different issuer's
    // unrelated registration that happens to share the same hash.
    mapping(bytes32 => bool) public revokedCredentials;

    event CredentialRegistered(
        bytes32 indexed credentialHash,
        address indexed issuer,
        uint256 issuedAt
    );
    event IssuerAccredited(address indexed issuer);
    event IssuerRevoked(address indexed issuer);
    event CredentialRevoked(
        bytes32 indexed credentialHash,
        address indexed issuer,
        address indexed revokedBy,
        uint256 revokedAt
    );

    /// @param initialOwner Address that controls issuer accreditation. This
    /// address is automatically accredited as an issuer too, so a fresh
    /// deployment is immediately usable (e.g. by the backend's own service
    /// account) without a separate bootstrap transaction.
    constructor(address initialOwner) Ownable(initialOwner) {
        accreditedIssuers[initialOwner] = true;
        emit IssuerAccredited(initialOwner);
    }

    modifier onlyAccreditedIssuer() {
        require(accreditedIssuers[msg.sender], "not an accredited issuer");
        _;
    }

    /// @notice Disabled. Ownable's renounceOwnership() would let issuer
    /// governance disappear entirely (no one could ever accredit or revoke
    /// an issuer again) with a single transaction and no two-step
    /// confirmation -- an irreversible mistake this contract has no
    /// legitimate reason to allow. Use transferOwnership() (two-step, via
    /// Ownable2Step) to hand off control instead.
    function renounceOwnership() public view override onlyOwner {
        revert("YouthChainRegistry: ownership renouncement is disabled");
    }

    /// @notice Grant an address permission to register credentials.
    function accreditIssuer(address issuer) external onlyOwner {
        accreditedIssuers[issuer] = true;
        emit IssuerAccredited(issuer);
    }

    /// @notice Revoke an address's permission to register credentials.
    function revokeIssuer(address issuer) external onlyOwner {
        accreditedIssuers[issuer] = false;
        emit IssuerRevoked(issuer);
    }

    /// @notice Derives the storage key a given (issuer, credentialHash)
    /// pair is stored under. Exposed publicly (not just used internally)
    /// so off-chain tooling never has to replicate this encoding itself
    /// and risk a subtle mismatch (abi.encode vs abi.encodePacked,
    /// argument order, ...) against what the contract actually computes.
    function credentialKey(address issuer, bytes32 credentialHash) public pure returns (bytes32) {
        return keccak256(abi.encode(issuer, credentialHash));
    }

    /// @notice Register a new credential hash on-chain, under the calling
    /// issuer's own slot (see credentialKey()) -- does not touch, and
    /// cannot collide with, any other issuer's registration of the same
    /// hash.
    /// @param credentialHash SHA-256 hash of the credential file.
    function registerCredential(bytes32 credentialHash)
        external
        onlyAccreditedIssuer
        returns (bytes32)
    {
        require(credentialHash != bytes32(0), "empty hash");
        bytes32 key = credentialKey(msg.sender, credentialHash);
        require(credentials[key].issuedAt == 0, "already exists");

        credentials[key] = Credential({
            credentialHash: credentialHash,
            issuer: msg.sender,
            issuedAt: block.timestamp
        });

        emit CredentialRegistered(credentialHash, msg.sender, block.timestamp);
        return credentialHash;
    }

    /// @notice Check if THIS issuer registered this exact hash (regardless
    /// of whether it has since been revoked -- see isValid() for that).
    /// Deliberately takes `issuer` explicitly rather than searching across
    /// every accredited issuer for a match -- see credentialKey()'s own
    /// docstring for why "did this specific issuer register this
    /// document" is the actual claim this platform makes, not the
    /// ambiguous (and, before this fix, hijackable) "has anyone".
    function isRegistered(address issuer, bytes32 credentialHash) external view returns (bool) {
        return credentials[credentialKey(issuer, credentialHash)].issuedAt != 0;
    }

    /// @notice Revoke a previously-registered credential (e.g. found to be
    /// fraudulent, or issued in error). Does not delete or mutate the
    /// original Credential record -- who issued it and when remains a
    /// permanent fact -- it only marks it as no longer currently valid.
    /// Owner-only rather than issuer-only: a fraud/error finding reaching
    /// this contract has already gone through the platform's own admin
    /// review, and an issuer should not be able to unilaterally erase
    /// evidence of their own mistake or misconduct by revoking it
    /// themselves. Takes `issuer` explicitly (not just the hash) for the
    /// same reason isRegistered()/isValid() do -- the owner must specify
    /// exactly whose registration of this hash is being revoked, now that
    /// more than one issuer can hold a registration for the same hash.
    function revokeCredential(address issuer, bytes32 credentialHash) external onlyOwner {
        bytes32 key = credentialKey(issuer, credentialHash);
        require(credentials[key].issuedAt != 0, "credential does not exist");
        require(!revokedCredentials[key], "already revoked");
        revokedCredentials[key] = true;
        emit CredentialRevoked(credentialHash, issuer, msg.sender, block.timestamp);
    }

    /// @notice The check a verifier should actually rely on: THIS issuer
    /// registered this hash, AND that registration is not revoked.
    /// isRegistered() alone only tells you a record exists, not whether
    /// it should still be trusted.
    function isValid(address issuer, bytes32 credentialHash) external view returns (bool) {
        bytes32 key = credentialKey(issuer, credentialHash);
        return credentials[key].issuedAt != 0 && !revokedCredentials[key];
    }
}
