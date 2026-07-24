module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        path: ".",
        message: "node preflight.js"
      }
    },
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "app",
        message: "python app.py",
        on: [{
          event: "/Uvicorn running on (http:\\/\\/[0-9.]+:[0-9]+) \\(Press/",
          done: true
        }]
      }
    },
    {
      method: "shell.run",
      params: {
        path: ".",
        message: "node stable_ui_server.js --upstream http://127.0.0.1:4200 --host 127.0.0.1 --port 0",
        on: [{
          event: "/Alexandria stable interface \\([^)]*\\): (http:\\/\\/[0-9.]+:[0-9]+\\/)/",
          done: true
        }]
      }
    },
    {
      method: "local.set",
      params: {
        url: "{{input.event[1]}}"
      }
    }
  ]
}
