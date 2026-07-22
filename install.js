module.exports = {
  run: [{
    method: "shell.run",
    params: {
      message: "uv cache clean"
    }
  }, {
    method: "shell.run",
    params: {
      path: "app",
      message: "python -m venv env"
    }
  }, {
    when: "{{platform === 'darwin' && arch === 'arm64'}}",
    method: "shell.run",
    params: {
      venv: "env",
      path: "app",
      message: [
        "uv pip uninstall google-genai qwen-tts",
        "uv pip install -r requirements-apple-silicon.txt"
      ]
    }
  }, {
    when: "{{!(platform === 'darwin' && arch === 'arm64')}}",
    method: "shell.run",
    params: {
      venv: "env",
      path: "app",
      message: [
        "uv pip uninstall google-genai",
        "uv pip install -r requirements.txt",
        "uv pip install qwen-tts==0.1.1"
      ]
    }
  }, {
    method: "script.start",
    params: {
      uri: "torch.js",
      params: {
        path: "app",
        venv: "env",
        flashattention: true
      }
    }
  }, {
    method: "notify",
    params: {
      html: "Installation Complete! Click 'Start' to launch the application."
    }
  }]
}
