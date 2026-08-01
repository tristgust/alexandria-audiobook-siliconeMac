const runtimeEnvironment = {
  ALEXANDRIA_RUNTIME_PYTHON: "{{path.resolve(cwd, 'app/env/bin/python')}}",
  ALEXANDRIA_CONFIG_PATH: "{{path.resolve(cwd, 'config.json')}}",
  ALEXANDRIA_LEGACY_ROOT_DIR: "{{path.resolve(cwd)}}",
}

module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        shell: "{{which('bash')}}",
        env: runtimeEnvironment,
        path: ".",
        message: "node preflight.js"
      }
    },
    {
      method: "shell.run",
      params: {
        shell: "{{which('bash')}}",
        env: runtimeEnvironment,
        path: ".",
        message: "node backend_runtime.js --host 127.0.0.1 --port 4200 --python app/env/bin/python --app app/app.py --config config.json",
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
        url: "{{input.event[1] + '?pinokio_reload=' + input.id}}"
      }
    }
  ]
}
