import { render, screen } from "@testing-library/react";

describe("frontend test environment", () => {
  it("renders React in jsdom with jest-dom matchers", () => {
    render(<p>Test environment ready</p>);

    expect(screen.getByText("Test environment ready")).toBeInTheDocument();
  });
});
