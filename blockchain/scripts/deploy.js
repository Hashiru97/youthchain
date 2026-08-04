import hre from "hardhat";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const { ethers, network } = hre;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function main() {
    const [deployer] = await ethers.getSigners();
    // The deployer is auto-accredited as an issuer by the contract's
    // constructor (see YouthChainRegistry.sol) — override with
    // OWNER_ADDRESS if a different address should control accreditation.
    const initialOwner = process.env.OWNER_ADDRESS || deployer.address;

    const Registry = await ethers.getContractFactory("YouthChainRegistry");
    const registry = await Registry.deploy(initialOwner);

    await registry.waitForDeployment();
    const address = await registry.getAddress();

    console.log("YouthChainRegistry deployed to:", address);
    console.log("Owner / initial accredited issuer:", initialOwner);

    const configPath = path.join(__dirname, "..", "deployed.json");
    fs.writeFileSync(
        configPath,
        JSON.stringify(
            {
                address,
                // Actual network name, not a hardcoded literal — a deploy
                // to any network other than localhost previously would have
                // silently written the wrong value here.
                network: network.name,
            },
            null,
            2
        )
    );
    console.log(`Wrote ${configPath}`);
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
