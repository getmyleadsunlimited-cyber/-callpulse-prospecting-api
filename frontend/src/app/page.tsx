import { serverApi } from "@/lib/server-api";
import { getServerSessionToken } from "@/lib/session";
import { RecoveryDashboard } from "@/components/recovery-dashboard";
import { ErrorState } from "@/components/states";

interface Prospect {
  id: number;
  email: string;
  company_name: string;
}

interface Campaign {
  id: number;
}

interface Delivery {
  id: number;
}

interface DashboardStats {
  prospectsCount: number;
  activeCampaigns: number;
  recoveredLeads: "not-available" | number;
  deliveriesCount: number;
  prospectDetails: Array<{
    id: number;
    email: string;
    company_name: string;
    campaignCount: number;
    deliveryCount: number;
  }>;
}

async function fetchDashboardData(): Promise<DashboardStats | null> {
  try {
    // Fetch all prospects
    const prospects = await serverApi<Prospect[]>("/prospects");
    const prospectsCount = prospects.length;

    let activeCampaigns = 0;
    let deliveriesCount = 0;
    const prospectDetails = [];

    // For each prospect, count campaigns and deliveries
    for (const prospect of prospects) {
      let campaignCount = 0;
      let deliveryCount = 0;

      try {
        // Get campaigns for this prospect
        const campaigns = await serverApi<Campaign[]>(`/prospects/${prospect.id}/campaigns`);
        campaignCount = campaigns.length;
        activeCampaigns += campaignCount;

        // For each campaign, count deliveries
        for (const campaign of campaigns) {
          try {
            const deliveries = await serverApi<Delivery[]>(`/campaigns/${campaign.id}/deliveries`);
            deliveryCount += deliveries.length;
          } catch {
            // If campaign deliveries fail, skip this campaign
          }
        }

        deliveriesCount += deliveryCount;
      } catch {
        // If campaigns fail for a prospect, skip this prospect
      }

      if (campaignCount > 0 || deliveryCount > 0) {
        prospectDetails.push({
          id: prospect.id,
          email: prospect.email,
          company_name: prospect.company_name,
          campaignCount,
          deliveryCount,
        });
      }
    }

    return {
      prospectsCount,
      activeCampaigns,
      recoveredLeads: "not-available",
      deliveriesCount,
      prospectDetails,
    };
  } catch (error) {
    console.error("Failed to fetch dashboard data:", error);
    return null;
  }
}

export default async function HomePage() {
  const token = await getServerSessionToken();

  if (!token) {
    return (
      <ErrorState
        title="Session expired"
        detail="Your session has expired. Please sign in again."
      />
    );
  }

  const data = await fetchDashboardData();

  if (data === null) {
    return (
      <ErrorState
        title="Failed to load dashboard"
        detail="Could not connect to the API. Please try refreshing the page."
      />
    );
  }

  return <RecoveryDashboard initialData={data} />;
}
