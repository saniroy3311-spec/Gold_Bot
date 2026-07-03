module.exports = {
  apps: [
    {
      name: "client-dashboard",
      cwd: "/root/Bot-v10",
      script: "uvicorn",
      args: "dashboard.main:app --host 0.0.0.0 --port 8082",
      interpreter: "python3",
      env: {
        // SECURITY: real credentials were removed here — this repo copy had
        // your live Turso DB token and dashboard password hardcoded in plain
        // text. Both should be treated as compromised and rotated. Fill in
        // the real values locally on the VPS only — never commit this file.
        TURSO_URL: "libsql://YOUR_DB_NAME.aws-ap-south-1.turso.io",
        TURSO_TOKEN: "YOUR_TURSO_TOKEN",
        DASHBOARD_USER: "admin",
        DASHBOARD_PASS: "YOUR_DASHBOARD_PASSWORD"
      }
    }
  ]
}
