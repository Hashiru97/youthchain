import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function loadArtifact(relPath) {
    const full = path.join(
        __dirname, "..", "node_modules", "@safe-global", "safe-contracts", "build", "artifacts", relPath
    );
    const { abi, bytecode } = JSON.parse(fs.readFileSync(full, "utf-8"));
    return { abi, bytecode };
}

/**
 * Deploys the real, official Safe v1.4.1 core contracts (Safe singleton,
 * SafeProxyFactory, MultiSend, MultiSendCallOnly,
 * CompatibilityFallbackHandler) fresh, from @safe-global/safe-contracts's
 * own compiled ABI+bytecode -- the actual audited Safe contracts, not a
 * mock or a simplified stand-in for them.
 *
 * ONLY for local/throwaway networks. @safe-global/protocol-kit already
 * knows the real, canonical addresses of these same contracts on every
 * real public network (mainnet, Sepolia, Polygon, ...) via
 * @safe-global/safe-deployments -- each deployed once, at the same
 * address, shared by every Safe that ever gets created on that chain. A
 * fresh local Hardhat node has none of that shared history, so this
 * recreates the minimum subset protocol-kit needs to function against
 * it -- exactly what a fresh local chain needs regardless of which
 * project is using it, not something specific to this registry.
 *
 * Returns a `contractNetworks` object shaped for protocol-kit's
 * SafeConfig (see @safe-global/protocol-kit's ContractNetworksConfig
 * type), keyed by this network's chainId.
 */
export async function deployLocalSafeInfra(ethers, deployerSigner) {
    const network = await ethers.provider.getNetwork();
    const chainId = network.chainId.toString();

    async function deploy(relPath, ...args) {
        const { abi, bytecode } = loadArtifact(relPath);
        const factory = new ethers.ContractFactory(abi, bytecode, deployerSigner);
        const contract = await factory.deploy(...args);
        await contract.waitForDeployment();
        return await contract.getAddress();
    }

    const safeSingletonAddress = await deploy("contracts/Safe.sol/Safe.json");
    const safeProxyFactoryAddress = await deploy("contracts/proxies/SafeProxyFactory.sol/SafeProxyFactory.json");
    const multiSendAddress = await deploy("contracts/libraries/MultiSend.sol/MultiSend.json");
    const multiSendCallOnlyAddress = await deploy("contracts/libraries/MultiSendCallOnly.sol/MultiSendCallOnly.json");
    const fallbackHandlerAddress = await deploy("contracts/handler/CompatibilityFallbackHandler.sol/CompatibilityFallbackHandler.json");

    return {
        [chainId]: {
            safeSingletonAddress,
            safeProxyFactoryAddress,
            multiSendAddress,
            multiSendCallOnlyAddress,
            fallbackHandlerAddress,
        },
    };
}

// Hardhat's own well-known local test mnemonic -- the same one printed by
// `npx hardhat node` on startup and already used, at derivation index 0-2,
// by blockchain/test/YouthChainRegistry.test.js's `ethers.getSigners()`.
// PUBLIC, published in every Hardhat README -- real, working private keys
// are used here on purpose (not `ethers.getSigners()`'s JSON-RPC-managed
// accounts) because @safe-global/protocol-kit needs a raw signer capable
// of EIP-712 typed-data signing for Safe transaction confirmations, which
// this mnemonic gives directly and reliably. Never reuse these for
// anything but a local, throwaway chain nobody else can reach -- see
// deploySafe.js's own repeated warnings about this same boundary.
export const LOCAL_TEST_MNEMONIC =
    "test test test test test test test test test test test junk";

export function localTestWallet(ethers, index) {
    return ethers.HDNodeWallet.fromPhrase(LOCAL_TEST_MNEMONIC, "", `m/44'/60'/0'/0/${index}`);
}
