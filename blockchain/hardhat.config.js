// Hardhat 3 config (migrated from Hardhat 2 -- real gap found via a full
// program-wide review: `npm audit` on the old hardhat@2.x + hardhat-toolbox
// stack found 37 vulnerabilities, 16 high, with no safe incremental fix
// available -- every one of them required this major version bump.
// Live-verified end-to-end after migrating (real local node: deploy,
// registerCredential, checkRegistered, checkValid, revokeCredential all
// re-run against Hardhat 3 with identical behavior to Hardhat 2).
//
// Deliberately NOT `@nomicfoundation/hardhat-toolbox` (Hardhat 2's bundle,
// now a deprecated compatibility shim in the Hardhat 3 ecosystem) and
// deliberately NOT `@nomicfoundation/hardhat-toolbox-mocha-ethers` (the
// real Hardhat 3 successor bundle) either -- the toolbox bundle pulls in
// hardhat-ignition and hardhat-verify, which this project doesn't use (it
// has its own hand-written deploy/register/check/revoke scripts, no
// Etherscan verification), and which reintroduce old @ethersproject-v5
// transitive dependencies (including an `elliptic` advisory with no fix
// available upstream) that a minimal, hand-picked plugin set avoids
// entirely. `npm audit` after this migration: 3 vulnerabilities (1 high,
// no fix available), all inside mocha's own `diff`/`serialize-javascript`
// dependencies -- an upstream mocha issue unrelated to Hardhat, and about
// as good as it gets without replacing Mocha/Chai with Hardhat 3's newer
// node:test runner (a much larger rewrite of every test file, not just
// tooling config).
import hardhatEthersPlugin from "@nomicfoundation/hardhat-ethers";
import hardhatEthersChaiMatchersPlugin from "@nomicfoundation/hardhat-ethers-chai-matchers";
import hardhatMochaPlugin from "@nomicfoundation/hardhat-mocha";
import { configVariable } from "hardhat/config";
import * as dotenv from "dotenv";

dotenv.config();

/** @type import('hardhat/config').HardhatUserConfig */
const config = {
    plugins: [hardhatEthersPlugin, hardhatEthersChaiMatchersPlugin, hardhatMochaPlugin],
    solidity: {
        profiles: {
            default: { version: "0.8.20" },
        },
    },
    networks: {
        // The in-process simulated chain `npx hardhat test` uses by default
        // when no --network flag is given.
        hardhatMainnet: {
            type: "edr-simulated",
            chainType: "l1",
        },
        localhost: {
            type: "http",
            chainType: "l1",
            url: "http://127.0.0.1:8545",
            // No explicit `accounts` here on purpose: this network only
            // ever talks to a local, ephemeral Hardhat node, whose accounts
            // are the well-known, publicly-documented default test
            // mnemonic. That is fine for local development and MUST NEVER
            // be reused on any real network (see S-12 in the engineering
            // review — this exact mistake has caused real fund loss
            // elsewhere in the industry).
        },
        // Template for a real deployment target (Phase 9 "Production MVP"
        // milestone). Uncomment and set the corresponding vars in .env —
        // never hardcode a private key here or anywhere else in source.
        // configVariable() reads from the environment the same way
        // process.env did before (see hardhat/config's own resolution --
        // env vars always win over any keystore plugin), just resolved
        // lazily instead of eagerly at config-load time.
        // sepolia: {
        //     type: "http",
        //     chainType: "l1",
        //     url: configVariable("SEPOLIA_RPC_URL"),
        //     accounts: [configVariable("DEPLOYER_PRIVATE_KEY")],
        // },
    },
};

export default config;
