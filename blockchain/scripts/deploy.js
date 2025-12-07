import hre from "hardhat";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const { ethers } = hre;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function main() {
    const Registry = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Registry.deploy();

    await registry.waitForDeployment();
    const address = await registry.getAddress();

    console.log("YouthChainRegistry deployed to:", address);

    const configPath = path.join(__dirname, "..", "deployed.json");
    fs.writeFileSync(
        configPath,
        JSON.stringify(
            {
                address,
                network: "localhost",
            },
            null,
            2
        )
    );
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
