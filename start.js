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
        backend_url: "{{input.event[1]}}",
        url: "{{input.event[1]}}"
      }
    }
  ]
}
