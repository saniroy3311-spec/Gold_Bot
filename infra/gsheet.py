import os
import json
import requests
import pandas as pd

class GoogleSheetsLogger:
    def __init__(self, config):
        self.enabled = getattr(config, 'GSHEET_ENABLED', True)
        self.spreadsheet_id = getattr(config, 'GSHEET_SPREADSHEET_ID', '1Bq5JjCsUM6jiqlxQ8WxHLbt-uEOuzRFX_tRtfra-908')
        self.sheet_name = getattr(config, 'GSHEET_SHEET_NAME', 'Live_Bot_Trade_Logs')
        self.creds_file = getattr(config, 'GSHEET_CREDS_FILE', 'credentials.json')
        self.webhook_url = getattr(config, 'GSHEET_WEBHOOK_URL', '')
        self.client = None

        if self.enabled:
            self._init_client()

    def _init_client(self):
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            if os.path.exists(self.creds_file):
                scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                creds = Credentials.from_service_account_file(self.creds_file, scopes=scopes)
                self.client = gspread.authorize(creds)
                print("[GoogleSheets] Connected via credentials.json.")
        except Exception as e:
            print(f"[GoogleSheets] Creds check: {e}")

    def append_trade(self, trade_data: dict):
        row = [
            trade_data.get('trade_id', ''),
            trade_data.get('entry_time', ''),
            trade_data.get('exit_time', ''),
            trade_data.get('asset', ''),
            trade_data.get('timeframe', '15m'),
            trade_data.get('strategy', ''),
            trade_data.get('side', ''),
            trade_data.get('entry_price', 0.0),
            trade_data.get('exit_price', 0.0),
            trade_data.get('points_captured', 0.0),
            trade_data.get('pos_size', 0.0),
            trade_data.get('gross_pnl', 0.0),
            trade_data.get('fees_gst', 0.0),
            trade_data.get('tax_31_2_pct', 0.0),
            trade_data.get('net_pnl_usd', 0.0),
            trade_data.get('net_pnl_inr', 0.0),
            trade_data.get('running_equity', 0.0),
            trade_data.get('reason', ''),
            trade_data.get('status', 'CLOSED')
        ]

        # 1. Webhook write
        if self.webhook_url:
            try:
                resp = requests.post(self.webhook_url, json={'row': row}, timeout=6)
                if resp.status_code == 200:
                    print(f"[GoogleSheets] Trade {trade_data.get('trade_id')} appended via Webhook.")
                    return
            except Exception as e:
                print(f"[GoogleSheets Webhook Error]: {e}")

        # 2. Service account write
        if self.client:
            try:
                sheet = self.client.open_by_key(self.spreadsheet_id).worksheet(self.sheet_name)
                sheet.append_row(row, value_input_option='USER_ENTERED')
                print(f"[GoogleSheets] Trade {trade_data.get('trade_id')} appended via GSpread.")
                return
            except Exception as e:
                print(f"[GoogleSheets GSpread Error]: {e}")

        # 3. Local CSV fallback
        csv_file = "live_trade_logs.csv"
        df_row = pd.DataFrame([trade_data])
        if not os.path.exists(csv_file):
            df_row.to_csv(csv_file, index=False)
        else:
            df_row.to_csv(csv_file, mode='a', header=False, index=False)
        print(f"[Local Journal] Saved trade {trade_data.get('trade_id')} to {csv_file}")
