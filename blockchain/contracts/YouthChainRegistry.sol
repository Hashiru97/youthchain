// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title YouthChainRegistry
/// @notice Minimal on-chain credential registry used by the YouthChain platform.
///         Each credential is represented by a bytes32 hash (e.g. SHA-256 of the file).
contract YouthChainRegistry {
    struct Credential {
        bytes32 credentialHash;
        address issuer;
        uint256 issuedAt;
    }

    mapping(bytes32 => Credential) public credentials;

    event CredentialRegistered(
        bytes32 indexed credentialHash,
        address indexed issuer,
        uint256 issuedAt
    );

    /// @notice Register a new credential hash on-chain.
    /// @param credentialHash SHA-256 hash of the credential file.
    function registerCredential(bytes32 credentialHash) external returns (bytes32) {
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
