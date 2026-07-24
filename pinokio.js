const fs = require('fs')
const path = require('path')

module.exports = {
  version: "5.0",
  title: "Alexandria",
  description: "A tool that takes a text document containing a book or a novel, ingests it with an LLM to produce an annotated script, and then uses a TTS API to generate the voice lines, finally stitching them together into an audiobook in MP3 format.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    // Check running states
    let running = {
      install: info.running("install.js"),
      start: info.running("start.js"),
      preview: info.running("preview.js"),
      reset: info.running("reset.js"),
      update: info.running("update.js"),
      validate: info.running("validate.js")
    }

    // Check file existence states
    let installed = info.exists("app/env")

    // Handle running states first
    if (running.install) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Installing",
        href: "install.js"
      }]
    }

    if (running.start) {
      const local = info.local("start.js")
      const previewLocal = info.local("preview.js")
      const menu = []
      if (local && local.url) {
        menu.push({
          default: true,
          icon: "fa-solid fa-rocket",
          text: "Open Latest Tested Build",
          href: local.url,
        })
      } else {
        menu.push({
          default: true,
          icon: "fa-solid fa-terminal",
          text: "Starting Latest Tested Build",
          href: "start.js",
        })
      }
      if (running.preview && previewLocal && previewLocal.url) {
        menu.push({
          icon: "fa-solid fa-flask",
          text: "Open Read-only UI Preview",
          href: previewLocal.url,
        })
      } else if (!running.preview && local && local.url) {
        menu.push({
          icon: "fa-solid fa-flask",
          text: "Start Read-only UI Preview",
          href: "preview.js",
        })
      }
      menu.push({ icon: "fa-solid fa-terminal", text: "Runtime Terminal", href: "start.js" })
      if (running.preview) menu.push({ icon: "fa-solid fa-terminal", text: "Preview Terminal", href: "preview.js" })
      return menu
    }

    if (running.preview) {
      let local = info.local("preview.js")
      if (local && local.url) {
        return [{
          default: true,
          icon: "fa-solid fa-flask",
          text: "Open Read-only UI Preview",
          href: local.url,
        }, {
          icon: "fa-solid fa-terminal",
          text: "Preview Terminal",
          href: "preview.js",
        }]
      }
      return [{
        default: true,
        icon: "fa-solid fa-terminal",
        text: "Starting Read-only UI Preview",
        href: "preview.js",
      }]
    }

    if (running.reset) {
      return [{
        default: true,
        icon: "fa-solid fa-rotate-left",
        text: "Resetting",
        href: "reset.js"
      }]
    }

    if (running.update) {
      return [{
        default: true,
        icon: "fa-solid fa-arrows-rotate",
        text: "Updating",
        href: "update.js"
      }]
    }


    if (running.validate) {
      return [{
        default: true,
        icon: "fa-solid fa-shield-halved",
        text: "Validating Latest Build",
        href: "validate.js"
      }]
    }

    // STATE: NOT_INSTALLED - auto-run install
    if (!installed) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Install",
        href: "install.js"
      }]
    }

    // STATE: INSTALLED
    return [{
      default: true,
      icon: "fa-solid fa-power-off",
      text: "Start Latest Tested Build",
      href: "start.js"
    }, {
      icon: "fa-solid fa-shield-halved",
      text: "Validate Latest Build",
      href: "validate.js"
    }, {
      icon: "fa-solid fa-folder-open",
      text: "Open Voicelines",
      href: "voicelines"
    }, {
      icon: "fa-solid fa-arrows-rotate",
      text: "Update",
      href: "update.js"
    }, {
      icon: "fa-solid fa-plug",
      text: "Reinstall",
      href: "install.js"
    }, {
      icon: "fa-solid fa-rotate-left",
      text: "Reset",
      href: "reset.js"
    }]
  }
}
