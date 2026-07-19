from flask import Flask, request, jsonify
import subprocess, os, hashlib, time

app = Flask(__name__)

# 密钥从环境变量读取，不在代码中硬编码
SECRET = os.environ.get("FUND_PREDICT_SECRET", "fund_predict_2026")

@app.route("/")
def index():
    html_path = os.path.expanduser("~/mysite/基金预测.html")
    if os.path.exists(html_path):
        return open(html_path, encoding="utf-8").read()
    return "<h1>基金预测还未生成，请先运行一次脚本</h1>"

@app.route("/chart.min.js")
def chart_js():
    js_path = os.path.expanduser("~/mysite/chart.min.js")
    if os.path.exists(js_path):
        return open(js_path, "rb").read(), 200, {"Content-Type": "application/javascript"}
    return "/* not found */", 404

@app.route("/run", methods=["GET", "POST"])
def run_predictor():
    # 验证密钥
    key = request.args.get("key") or request.form.get("key", "")
    if key != SECRET:
        return jsonify({"error": "invalid key"}), 403

    script = os.path.expanduser("~/fund_predictor_cloud/fund_predictor.py")
    # Try python3 first, fallback to python
    py = "python3" if os.path.exists("/usr/bin/python3") else "python"
    try:
        result = subprocess.run(
            [py, script],
            capture_output=True, text=True, timeout=300
        )
        return jsonify({
            "status": "ok",
            "output": result.stdout[-2000:] if result.stdout else "",
            "error": result.stderr[-500:] if result.stderr else "",
            "returncode": result.returncode
        })
    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "traceback": traceback.format_exc()}), 500
