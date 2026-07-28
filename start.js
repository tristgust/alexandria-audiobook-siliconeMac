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
        path: ".",
        message: "node backend_runtime.js --host 127.0.0.1 --port 4200 --python app/env/bin/python --app app/app.py",
        on: [{
          event: "/Alexandria backend ready: (http:\\/\\/[0-9.]+:[0-9]+\\/)/",
          done: true
        }]
      }
    },
    {
      method: "local.set",
      params: {
        backend_url: "{{input.event[1]}}"
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
        stable_url: "{{input.event[1]}}"
      }
    },
    {
      method: "shell.run",
      params: {
        path: ".",
        message: "node new_ui_server.js --upstream http://127.0.0.1:4200 --host 127.0.0.1 --port 0",
        on: [{
          event: "/Alexandria new writable interface: (http:\\/\\/[0-9.]+:[0-9]+\\/)/",
          done: true
        }]
      }
    },
    {
      method: "local.set",
      params: {
        new_url: "{{input.event[1]}}",
        url: "{{input.event[1]}}"
      }
    }
  ]
}
