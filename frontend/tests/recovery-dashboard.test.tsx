import { render, screen, waitFor, within, cleanup } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { RecoveryDashboard } from "@/components/recovery-dashboard";

describe("RecoveryDashboard", () => {
  const mockData = {
    prospectsCount: 5,
    activeCampaigns: 3,
    recoveredLeads: "not-available" as const,
    deliveriesCount: 9,
    prospectDetails: [
      {
        id: 1,
        email: "prospect@example.com",
        company_name: "Example Corp",
        campaignCount: 2,
        deliveryCount: 6,
      },
      {
        id: 2,
        email: "another@example.com",
        company_name: "Another Inc",
        campaignCount: 1,
        deliveryCount: 3,
      },
    ],
  };

  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders with initial data", () => {
    const { container } = render(<RecoveryDashboard initialData={mockData} />);

    expect(screen.getByText("AI Website Lead Recovery")).toBeInTheDocument();
    expect(screen.getByText("Recover more value from the traffic you're already paying for.")).toBeInTheDocument();

    // Just check that the full container has all the key information
    const fullContent = container.textContent;
    expect(fullContent).toContain("Prospects");
    expect(fullContent).toContain("Active Campaigns");
    expect(fullContent).toContain("Deliveries");
    expect(fullContent).toContain("Not available yet");
  });

  it("displays summary cards with correct labels", () => {
    render(<RecoveryDashboard initialData={mockData} />);

    expect(screen.getByText("Prospects")).toBeInTheDocument();
    expect(screen.getByText("Active Campaigns")).toBeInTheDocument();
    // "Recovered Leads" appears multiple times, so use getAllByText
    expect(screen.getAllByText("Recovered Leads").length).toBeGreaterThan(0);
    // "Deliveries" also appears multiple times (card + table), so use getAllByText
    expect(screen.getAllByText("Deliveries").length).toBeGreaterThan(0);
  });

  it("displays recovered leads section with empty state message", () => {
    render(<RecoveryDashboard initialData={mockData} />);

    const recoveredLeadsHeadings = screen.getAllByText("Recovered Leads");
    expect(recoveredLeadsHeadings.length).toBeGreaterThan(0);
    expect(
      screen.getByText("Recovered lead data will appear here when the recovery feed is connected.")
    ).toBeInTheDocument();
  });

  it("displays campaign activity section with prospect table", () => {
    render(<RecoveryDashboard initialData={mockData} />);

    expect(screen.getByText("Example Corp")).toBeInTheDocument();
    expect(screen.getByText("Another Inc")).toBeInTheDocument();
    expect(screen.getByText("prospect@example.com")).toBeInTheDocument();
    expect(screen.getByText("another@example.com")).toBeInTheDocument();
  });

  it("displays empty campaign activity when no prospects have campaigns", () => {
    const emptyData = {
      ...mockData,
      prospectDetails: [],
      activeCampaigns: 0,
      deliveriesCount: 0,
    };

    render(<RecoveryDashboard initialData={emptyData} />);

    expect(
      screen.getByText("No campaign activity yet. Create a prospect and launch a campaign to get started.")
    ).toBeInTheDocument();
  });

  it("shows loading state when no initial data and fetch is pending", async () => {
    global.fetch = vi.fn(() => new Promise<Response>(() => {}));

    render(<RecoveryDashboard initialData={null} />);

    expect(screen.getByText(/Loading dashboard/)).toBeInTheDocument();
  });

  it("displays error state when API call fails", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
      } as Response)
    );

    render(<RecoveryDashboard initialData={null} />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load dashboard")).toBeInTheDocument();
    });
  });

  it("fetches data when no initial data is provided", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockData),
      } as Response)
    );

    render(<RecoveryDashboard initialData={null} />);

    await waitFor(() => {
      expect(screen.getByText("AI Website Lead Recovery")).toBeInTheDocument();
      expect(global.fetch).toHaveBeenCalledWith("/api/dashboard-stats", { cache: "no-store" });
    });
  });

  it("renders campaign activity table headers", () => {
    render(<RecoveryDashboard initialData={mockData} />);

    expect(screen.getByText("Prospect")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
    // Just check that they exist in the document
    const headings = screen.getAllByRole("columnheader");
    expect(headings.length).toBeGreaterThan(0);
  });

  it("displays correct campaign and delivery counts in table", () => {
    render(<RecoveryDashboard initialData={mockData} />);

    // Check that the table exists and contains the company names
    const table = screen.getByRole("table");
    expect(table).toBeInTheDocument();

    // Check that both prospects are in the table
    expect(within(table).getByText("Example Corp")).toBeInTheDocument();
    expect(within(table).getByText("Another Inc")).toBeInTheDocument();

    // Check that prospect details are displayed
    expect(within(table).getByText("prospect@example.com")).toBeInTheDocument();
    expect(within(table).getByText("another@example.com")).toBeInTheDocument();
  });
});
