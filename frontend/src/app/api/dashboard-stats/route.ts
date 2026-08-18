import { serverApi } from "@/lib/server-api";
import { NextResponse } from "next/server";

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

export async function GET(): Promise<NextResponse<DashboardStats>> {
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

    const stats: DashboardStats = {
      prospectsCount,
      activeCampaigns,
      recoveredLeads: "not-available",
      deliveriesCount,
      prospectDetails,
    };

    return NextResponse.json(stats);
  } catch (error) {
    console.error("Dashboard API error:", error);
    return NextResponse.json(
      {
        prospectsCount: 0,
        activeCampaigns: 0,
        recoveredLeads: "not-available" as const,
        deliveriesCount: 0,
        prospectDetails: [],
      },
      { status: 500 }
    );
  }
}
