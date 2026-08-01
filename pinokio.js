module.exports = {
  version: "5.0",
  title: "Alexandria",
  description: "Create and produce source-faithful multi-voice audiobooks in Alexandria.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    const running = {
      install: info.running("install.js"),
      start: info.running("start.js"),
      reset: info.running("reset.js"),
      update: info.running("update.js"),
      validate: info.running("validate.js"),
    }
    const installed = info.exists("app/env")

    if (running.install) return [{
      default: true,
      icon: "fa-solid fa-plug",
      text: "Installing",
      href: "install.js",
    }]
    if (running.reset) return [{
      default: true,
      icon: "fa-solid fa-rotate-left",
      text: "Resetting",
      href: "reset.js",
    }]
    if (running.update) return [{
      default: true,
      icon: "fa-solid fa-arrows-rotate",
      text: "Updating",
      href: "update.js",
    }]
    if (running.validate) return [{
      default: true,
      icon: "fa-solid fa-shield-halved",
      text: "Validating Alexandria",
      href: "validate.js",
    }]

    if (running.start) {
      const runtime = info.local("start.js") || {}
      if (runtime.url) {
        const backendUrl = runtime.backend_url || runtime.url
        const separator = backendUrl.includes("?") ? "&" : "?"
        const openUrl = `${backendUrl}${separator}pinokio_reload=${Date.now()}`
        return [{
          default: true,
          icon: "fa-solid fa-book-open",
          text: "Open Alexandria",
          href: openUrl,
        }, {
          icon: "fa-solid fa-terminal",
          text: "Alexandria Terminal",
          href: "start.js",
        }]
      }
      return [{
        default: true,
        icon: "fa-solid fa-terminal",
        text: "Starting Alexandria",
        href: "start.js",
      }]
    }

    if (!installed) return [{
      default: true,
      icon: "fa-solid fa-plug",
      text: "Install",
      href: "install.js",
    }]

    return [{
      default: true,
      icon: "fa-solid fa-power-off",
      text: "Start Alexandria",
      href: "start.js",
    }, {
      icon: "fa-solid fa-shield-halved",
      text: "Validate Alexandria",
      href: "validate.js",
    }, {
      icon: "fa-solid fa-folder-open",
      text: "Open Voicelines",
      href: "voicelines",
    }, {
      icon: "fa-solid fa-arrows-rotate",
      text: "Update",
      href: "update.js",
    }, {
      icon: "fa-solid fa-plug",
      text: "Reinstall",
      href: "install.js",
    }, {
      icon: "fa-solid fa-rotate-left",
      text: "Reset",
      href: "reset.js",
    }]
  },
}
