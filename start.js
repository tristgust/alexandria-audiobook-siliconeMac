module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        path: ".",
        message: "env ALEXANDRIA_RUNTIME_PYTHON=/Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python ALEXANDRIA_CONFIG_PATH=/Users/tristan/pinokio/api/alexandria-audiobook.git/app/config.json ALEXANDRIA_LEGACY_ROOT_DIR=/Users/tristan/pinokio/api/alexandria-audiobook.git node preflight.js"
      }
    },
    {
      method: "shell.run",
      params: {
        path: ".",
        message: "env ALEXANDRIA_CONFIG_PATH=/Users/tristan/pinokio/api/alexandria-audiobook.git/app/config.json ALEXANDRIA_LEGACY_ROOT_DIR=/Users/tristan/pinokio/api/alexandria-audiobook.git node backend_runtime.js --host 127.0.0.1 --port 4200 --python /Users/tristan/pinokio/api/alexandria-audiobook.git/app/env/bin/python --app app/app.py",
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
