import "@testing-library/jest-dom"

// jsdom does not include fetch; define a stub so tests can mock it with jest.spyOn
if (!global.fetch) {
  global.fetch = jest.fn()
}
