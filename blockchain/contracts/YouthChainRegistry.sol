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

    mapping(bytes32 => Credential) public credentials;
    mapping(address => bool) public accreditedIssuers;
    // Real gap found via a full-codebase review: this registry had no way
    // to invalidate a credential once written -- for a platform whose
    // stated mission includes "solving fake credentials," a fraudulent or
    // erroneously-issued credential stayed permanently "valid" forever
    // with no recourse. A separate mapping (not deleting/mutating the
    // Credential struct itself) so the original registration -- who
    // issued it and when -- remains a permanent, auditable fact even
    // after revocation; only its current trust status changes.
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

    /// @notice Register a new credential hash on-chain.
    /// @param credentialHash SHA-256 hash of the credential file.
    function registerCredential(bytes32 credentialHash)
        external
        onlyAccreditedIssuer
        returns (bytes32)
    {
        require(credentialHash != bytes32(0), "empty hash");
        require(credentials[credentialHash].issuedAt == 0, "already exists");

        credentials[credentialHash] = Credential({
            credentialHash: credentialHash,
            issuer: msg.sender,
            issuedAt: block.timestamp
        });

        emit CredentialRegistered(credentialHash, msg.sender, block.timestamp);
        return credentialHash;
    }

    /// @notice Check if a credential hash is already recorded (regardless
    /// of whether it has since been revoked -- see isValid() for that).
    function isRegistered(bytes32 credentialHash) external view returns (bool) {
        return credentials[credentialHash].issuedAt != 0;
    }

    /// @notice Revoke a previously-registered credential (e.g. found to be
    /// fraudulent, or issued in error). Does not delete or mutate the
    /// original Credential record -- who issued it and when remains a
    /// permanent fact -- it only marks it as no longer currently valid.
    /// Owner-only rather than issuer-only: a fraud/error finding reaching
    /// this contract has already gone through the platform's own admin
    /// review, and an issuer should not be able to unilaterally erase
    /// evidence of their own mistake or misconduct by revoking it
    /// themselves.
    function revokeCredential(bytes32 credentialHash) external onlyOwner {
        require(credentials[credentialHash].issuedAt != 0, "credential does not exist");
        require(!revokedCredentials[credentialHash], "already revoked");
        revokedCredentials[credentialHash] = true;
        emit CredentialRevoked(credentialHash, msg.sender, block.timestamp);
    }

    /// @notice The check a verifier should actually rely on: registered
    /// AND not revoked. isRegistered() alone only tells you a record
    /// exists, not whether it should still be trusted.
    function isValid(bytes32 credentialHash) external view returns (bool) {
        return credentials[credentialHash].issuedAt != 0 && !revokedCredentials[credentialHash];
    }
}
