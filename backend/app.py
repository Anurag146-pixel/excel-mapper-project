from flask import Flask, request, jsonify
from flask_cors import CORS

from loader import load_file
from compiler import TemplateCompiler

app = Flask(__name__)
CORS(app)  # allows frontend JS to talk to backend


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


@app.route("/compile-template", methods=["POST"])
def compile_template():
    """
    Accepts:
      - file (csv / xlsx)
      - template (JSON)
    Returns:
      - compiled extraction plan
    """

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    template = request.json.get("template") if request.is_json else None

    if not template:
        return jsonify({"error": "Template JSON missing"}), 400

    try:
        # Load Excel/CSV
        matrix = load_file(file)

        # Compile template
        compiler = TemplateCompiler(template)
        compiled_plan = compiler.compile()

        return jsonify({
            "message": "Template compiled successfully",
            "compiled_plan": compiled_plan
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
