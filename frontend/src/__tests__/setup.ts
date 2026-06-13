import "@testing-library/jest-dom"

// jsdom does not include fetch; define a stub so tests can mock it with jest.spyOn
if (!global.fetch) {
  global.fetch = jest.fn()
}

// jsdom lacks matchMedia / ResizeObserver, which StickyHorizontalScroll (wrapped by the
// shared <Table>) calls inside effects. Polyfill them so Table-based components can mount.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

if (!global.ResizeObserver) {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}
