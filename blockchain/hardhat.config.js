import "@nomicfoundation/hardhat-toolbox";
import * as dotenv from "dotenv";

dotenv.config();

/** @type import('hardhat/config').HardhatUserConfig */
const config = {
    solidity: "0.8.20",
    networks: {
        localhost: {
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
        // sepolia: {
        //     url: process.env.SEPOLIA_RPC_URL || "",
        //     accounts: process.env.DEPLOYER_PRIVATE_KEY
        //         ? [process.env.DEPLOYER_PRIVATE_KEY]
        //         : [],
        // },
    },
};

export default config;
