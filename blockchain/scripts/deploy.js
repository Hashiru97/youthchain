import { network } from "hardhat";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const connection = await network.create();
const { ethers } = connection;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const configPath = path.join(__dirname, "..", "deployed.json");

async function main() {
    // Re-running this script against a network that already has a live
    // deployment would silently spin up a brand-new, empty registry and
    // overwrite deployed.json — orphaning every credential/issuer already
    // registered on the old contract from every script/backend that reads
    // deployed.json afterward. Require an explicit opt-in to do that on
    // purpose. Local iteration (redeploying to the ephemeral in-process
    // hardhatMainnet chain) is unaffected since that chain — and its
    // "previous" deployed.json entry — doesn't persist across runs anyway.
    if (fs.existsSync(configPath)) {
        const existing = JSON.parse(fs.readFileSync(configPath, "utf-8"));
        if (existing.network === connection.networkName && !process.env.FORCE_REDEPLOY) {
            console.error(
                `❌ ERROR: deployed.json already has a "${connection.networkName}" deployment at ${existing.address}. ` +
                    "Re-running deploy.js would replace it and orphan every credential registered against it. " +
                    "Set FORCE_REDEPLOY=1 to deploy a new registry anyway."
            );
            process.exitCode = 1;
            return;
        }
    }

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

    fs.writeFileSync(
        configPath,
        JSON.stringify(
            {
                address,
                // Actual network name, not a hardcoded literal — a deploy
                // to any network other than localhost previously would have
                // silently written the wrong value here.
                network: connection.networkName,
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
