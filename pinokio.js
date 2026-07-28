module.exports = {
  version: "5.0",
  title: "Alexandria",
  description: "Create and produce audiobooks in either the stable interface or the rebuilt writable interface. A separate read-only QA preview remains available.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    const running = {
      install: info.running("install.js"),
      start: info.running("start.js"),
      preview: info.running("preview.js"),
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
      text: "Validating Builds",
      href: "validate.js",
    }]

    if (running.start || running.preview) {
      const production = info.local("start.js") || {}
      const preview = info.local("preview.js") || {}
      const menu = []

      if (production.new_url) menu.push({
        default: true,
        icon: "fa-solid fa-wand-magic-sparkles",
        text: "Open New Interface (Writable)",
        href: production.new_url,
      })
      if (production.stable_url) menu.push({
        default: !production.new_url,
        icon: "fa-solid fa-book-open",
        text: "Open Stable Build (Old UI)",
        href: production.stable_url,
      })
      if (running.start && !production.new_url && !production.stable_url) menu.push({
        default: true,
        icon: "fa-solid fa-terminal",
        text: "Starting Alexandria Interfaces",
        href: "start.js",
      })
      if (!running.start) menu.push({
        default: menu.length === 0,
        icon: "fa-solid fa-power-off",
        text: "Start Alexandria Interfaces",
        href: "start.js",
      })

      if (preview.preview_url) menu.push({
        icon: "fa-solid fa-flask",
        text: "Open Read-only QA Preview",
        href: preview.preview_url,
      })
      if (running.preview && !preview.preview_url) menu.push({
        icon: "fa-solid fa-terminal",
        text: "Starting Read-only QA Preview",
        href: "preview.js",
      })
      if (!running.preview) menu.push({
        icon: "fa-solid fa-flask",
        text: "Start Read-only QA Preview",
        href: "preview.js",
      })

      if (running.start) menu.push({
        icon: "fa-solid fa-terminal",
        text: "Alexandria Terminal",
        href: "start.js",
      })
      if (running.preview) menu.push({
        icon: "fa-solid fa-terminal",
        text: "QA Preview Terminal",
        href: "preview.js",
      })
      return menu
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
      text: "Start Alexandria Interfaces",
      href: "start.js",
    }, {
      icon: "fa-solid fa-flask",
      text: "Start Read-only QA Preview",
      href: "preview.js",
    }, {
      icon: "fa-solid fa-shield-halved",
      text: "Validate Builds",
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
