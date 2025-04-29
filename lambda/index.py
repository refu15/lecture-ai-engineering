# lambda/index.py
import json
import os
import re
import urllib.request # 標準ライブラリでHTTPリクエストを行う
import urllib.error   # エラーハンドリング用
from pyngrok import ngrok, conf
import nest_asyncio
from dotenv import load_dotenv
import subprocess

# --- 設定 ---
# FastAPIサーバーのエンドポイントURL (後でColabのngrok URLに置き換える)
FASTAPI_ENDPOINT_URL = os.environ.get("FASTAPI_ENDPOINT_URL", "YOUR_FASTAPI_NGROK_URL_HERE/generate")
MODEL_ID = os.environ.get("MODEL_ID", "google/gemma-2-2b-jpn-it") # これはFastAPI側で使うモデル名として残しても良い

# Lambda コンテキストからリージョンを抽出する関数 (不要になるが念のため残す)
def extract_region_from_arn(arn):
    match = re.search('arn:aws:lambda:([^:]+):', arn)
    if match:
        return match.group(1)
    return "us-east-1"

def lambda_handler(event, context):
    try:
        # 不要になったBedrockクライアント初期化を削除
        # global bedrock_client
        # if bedrock_client is None:
        #     region = extract_region_from_arn(context.invoked_function_arn)
        #     bedrock_client = boto3.client('bedrock-runtime', region_name=region)
        #     print(f"Initialized Bedrock client in region: {region}")

        print("Received event:", json.dumps(event))

        # Cognitoユーザー情報はそのまま
        user_info = None
        if 'requestContext' in event and 'authorizer' in event['requestContext']:
            user_info = event['requestContext']['authorizer']['claims']
            print(f"Authenticated user: {user_info.get('email') or user_info.get('cognito:username')}")

        # リクエストボディの解析
        body = json.loads(event['body'])
        message = body['message']
        conversation_history = body.get('conversationHistory', [])

        print("Processing message:", message)
        # print("Using model:", MODEL_ID) # FastAPI側に依存するのでコメントアウト

        # --- FastAPIサーバーへのリクエスト ---
        # FastAPIの /generate エンドポイントは単純なプロンプトを期待すると仮定
        # ここでは最新のユーザーメッセージをプロンプトとして使用する
        # 必要に応じて会話履歴を結合してプロンプトを作成するロジックに変更可能
        prompt_to_send = message

        # FastAPIへのリクエストペイロードを作成
        fastapi_payload = {
            "prompt": prompt_to_send,
            "max_new_tokens": 512, # 必要に応じて調整
            "temperature": 0.7,
            "top_p": 0.9
        }
        data = json.dumps(fastapi_payload).encode('utf-8')

        print(f"Sending request to FastAPI: {FASTAPI_ENDPOINT_URL}")
        print(f"Payload: {json.dumps(fastapi_payload)}")

        # HTTPリクエストを作成して送信
        req = urllib.request.Request(
            FASTAPI_ENDPOINT_URL,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        assistant_response = ""
        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    response_body = json.loads(response.read().decode('utf-8'))
                    print("FastAPI response:", json.dumps(response_body))
                    if response_body.get("generated_text"):
                        assistant_response = response_body["generated_text"]
                    else:
                        print("Error: 'generated_text' not found in FastAPI response")
                        raise Exception("FastAPI did not return generated text.")
                else:
                    print(f"Error: FastAPI server returned status code {response.status}")
                    print(f"Response body: {response.read().decode('utf-8')}")
                    raise Exception(f"FastAPI request failed with status {response.status}")
        except urllib.error.URLError as e:
            print(f"Error connecting to FastAPI server: {e}")
            raise Exception(f"Could not connect to the inference server: {e}")
        except urllib.error.HTTPError as e:
            print(f"HTTP Error from FastAPI server: {e.code}")
            try:
                error_body = e.read().decode('utf-8')
                print(f"Error details: {error_body}")
                # FastAPIからのエラー詳細を抽出試行
                error_detail = json.loads(error_body).get("detail", str(e))
            except Exception:
                error_detail = str(e)
            raise Exception(f"Inference server error: {error_detail}")


        # --- レスポンス処理 ---
        # アシスタントの応答を会話履歴に追加
        # (元のコードと同様に、リクエストに含めたメッセージリストを更新)
        updated_history = conversation_history.copy()
        updated_history.append({"role": "user", "content": message})
        updated_history.append({"role": "assistant", "content": assistant_response})

        # 成功レスポンスの返却
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "OPTIONS,POST"
            },
            "body": json.dumps({
                "success": True,
                "response": assistant_response,
                # 更新された会話履歴を返す
                "conversationHistory": updated_history
            })
        }

    except Exception as error:
        print("Lambda execution error:", str(error))
        import traceback
        traceback.print_exc() # 詳細なトレースバックを出力

        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "OPTIONS,POST"
            },
            "body": json.dumps({
                "success": False,
                "error": f"An error occurred: {str(error)}"
            })
        }

# .env ファイルから環境変数を読み込む
load_dotenv(dotenv_path='day1/.env')

# Ngrok設定
NGROK_TOKEN = os.getenv('NGROK_TOKEN')
if not NGROK_TOKEN:
  print("エラー: NGROK_TOKEN が .env ファイルに見つかりません。")
else:
  ngrok.set_auth_token(NGROK_TOKEN)
  conf.get_default().region = 'jp' # 日本リージョンを指定 (任意)

# 既存のngrokトンネルを閉じる試行
try:
  tunnels = ngrok.get_tunnels()
  for tunnel in tunnels:
    ngrok.disconnect(tunnel.public_url)
    print(f"切断: {tunnel.public_url}")
except Exception as e:
  print(f"既存トンネルの切断中にエラー: {e}")


# nest_asyncio を適用
nest_asyncio.apply()

# FastAPIサーバーをバックグラウンドで起動
# uvicornを直接実行 (day1/03_FastAPI/app.py を参照)
port = 8000
proc = subprocess.Popen([
    "python", "-m", "uvicorn",
    "day1.03_FastAPI.app:app", # モジュールパスで指定
    "--host", "0.0.0.0",
    "--port", str(port)
])
print(f"FastAPIサーバーをポート {port} で起動中...")

# ngrokトンネルを開く
try:
    public_url = ngrok.connect(port)
    print(f"✅ FastAPIサーバー公開URL (これを Lambda の FASTAPI_ENDPOINT_URL に設定):")
    print(f"   {public_url}")
    print("---")
    print("サーバーログは uvicorn の出力で確認してください。")
    print("Colabセルを停止するとサーバーも停止します。")

except Exception as e:
    print(f"❌ ngrokトンネルの起動に失敗しました: {e}")
    # FastAPIプロセスも終了させる
    proc.terminate()
    proc.wait()
