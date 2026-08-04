// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";

/// @title YouthChainRegistry
/// @notice Minimal on-chain credential registry used by the YouthChain platform.
///         Each credential is represented by a bytes32 hash (e.g. SHA-256 of the file).
/// @dev Access control added per engineering review finding S-11: the original
///      version let ANY address register a credential and be recorded as its
///      issuer. That was only safe by accident (the local Hardhat network isn't
///      publicly reachable) and would become a critical vulnerability the
///      moment this contract is deployed anywhere real. Only addresses the
///      contract owner has explicitly accredited may call registerCredential.
contract YouthChainRegistry is Ownable {
    struct Credential {
        bytes32 credentialHash;
        address issuer;
        uint256 issuedAt;
    }

    mapping(bytes32 => Credential) public credentials;
    mapping(address => bool) public accreditedIssuers;

    event CredentialRegistered(
        bytes32 indexed credentialHash,
        address indexed issuer,
        uint256 issuedAt
    );
    event IssuerAccredited(address indexed issuer);
    event IssuerRevoked(address indexed issuer);

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

    /// @notice Check if a credential hash is already recorded.
    function isRegistered(bytes32 credentialHash) external view returns (bool) {
        return credentials[credentialHash].issuedAt != 0;
    }
}
