import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';

import { AnalyticsComponent } from './analytics/analytics.component';
import { ECommerceComponent } from './e-commerce/e-commerce.component';
import { IntelligenceComponent } from './intelligence/intelligence.component';
import { OpportunitiesComponent } from './opportunities/opportunities.component';
import { SourcesComponent } from './sources/sources.component';
import { ExplorerComponent } from './explorer/explorer.component';

const routes: Routes = [
  {
    path: '',
    children: [
      // Crucix intelligence routes (home page)
      { path: 'brief',         component: IntelligenceComponent, data: { title: 'Intelligence Brief' } },
      { path: 'opportunities', component: OpportunitiesComponent, data: { title: 'Opportunity Pipeline' } },
      { path: 'sources',       component: SourcesComponent,      data: { title: 'Source Health' } },
      { path: 'explorer',      component: ExplorerComponent,     data: { title: 'Intelligence Explorer' } },
      // Template demo routes
      { path: 'e-commerce',    component: ECommerceComponent,    data: { title: 'e-Commerce' } },
      { path: 'analytics',     component: AnalyticsComponent,    data: { title: 'Analytics' } },
    ]
  }
];

@NgModule({
  imports: [RouterModule.forChild(routes)],
  exports: [RouterModule]
})
export class DashboardRoutingModule { }
