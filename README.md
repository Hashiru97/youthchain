# 🌍 YouthChain  
### **Verified Skills. Real Opportunities.**

YouthChain is a blockchain-backed employment ecosystem designed to **empower youth**, **increase employer trust**, and **eliminate credential fraud** through a unified digital identity and verifiable credential system.

Built for the DSTI Big 5 Hackathon — this project demonstrates a real, deployable platform combining:

- Secure API backend (Flask + SQLAlchemy)  
- Real-time job matching (Socket.IO + Skill Matching Engine)  
- Blockchain credential verification (Hardhat + Solidity)  
- Mobile App (Flutter) for youth employment access  
- Employer Web Dashboard for job posting & verification  

---

# 🚀 Features

### ✅ Youth Mobile App (Flutter)
- Registration + OTP login  
- Skill-based job matching (overlap score %)  
- Apply with CV & documents  
- Live updates when job postings change  
- Digital **Employment Passport** showing blockchain-verified credentials  

### ✅ Employer Dashboard (Flask Templates)
- Post jobs  
- View applicants  
- Verify credentials instantly  
- Real-time updates through WebSockets  

### ✅ Blockchain Credential Registry
- Credentials hashed using SHA-256  
- Hash stored on Ethereum (Hardhat local network)  
- Fraud-proof verification by comparing hash with stored credential  
- Integrated into backend `/issue_credential`  

---

# 🏛 System Architecture

              +---------------------+
              |   Flutter Mobile    |
              |     Application     |
              +----------+----------+
                         |
                         | REST / WebSocket
                         v
  +------------------------------------------------+
  |                    Flask API                   |
  |  Auth, Jobs, Applications, Passport, Issuance  |
  +----------------------+-------------------------+
                         |
                         | SQLAlchemy ORM
                         v
                +------------------+
                |   SQLite DB      |
                +------------------+
                         |
                         | SHA-256 hash
                         v
+---------------------------------------------------------------+
| Blockchain (Hardhat EVM) |
| CredentialRegistry.sol — stores credential hash + issuer |
+---------------------------------------------------------------+

---

# 📦 Project Structure

youthchain_project/
├── backend/
├── youthchain_app/ (Flutter)
├── blockchain/ (Hardhat)
├── README.md
└── RUN_THIS_FIRST.txt        

# 🧪 Quickstart for Judges

Follow these steps EXACTLY to run the system locally.

---

## **1️⃣ Start the Blockchain (Hardhat)**

```bash
cd blockchain
npx hardhat node 

Keep this terminal open.

Now deploy the smart contract:

npx hardhat run scripts/deploy.js --network localhost

You will see:
YouthChainRegistry deployed to: 0x5FbDB...

Start the Backend (Flask)
cd backend
pip install -r requirements.txt
python3 app.py

Backend runs on:

http://127.0.0.1:5000

Start the Flutter App
cd youthchain_app
flutter pub get
flutter run -d chrome

Environment Configuration

Create backend/.env using this template:
FLASK_ENV=development
SECRET_KEY=supersecretkey123

# Email (optional for OTP)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_EMAIL=your_email@gmail.com
SMTP_PASSWORD=your_app_password

# Blockchain RPC
RPC_URL=http://127.0.0.1:8545
CONTRACT_ADDRESS=0x5FbDB2315678afecb367f032d93F642f64180aa3

Flutter app config (lib/config.dart)
class Config {
  static const apiBase = "http://127.0.0.1:5000";
}

Issue a credential:
curl -X POST http://127.0.0.1:5000/issue_credential \
  -F "user_id=1" \
  -F "title=ENGI 316 Certificate" \
  -F "issuer=Cyprus International University" \
  -F "file=@/path/to/file.pdf"
  
  Then register it on-chain:
  cd blockchain
HASH=<paste_hash_here> \
npx hardhat run scripts/registerCredential.js --network localhost
