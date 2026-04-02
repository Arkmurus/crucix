import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClientModule } from '@angular/common/http';

import { DashboardRoutingModule } from './dashboard-routing.module';
import { MatModule } from '../appModules/mat.module';

// Template components
import { ECommerceComponent } from './e-commerce/e-commerce.component';
import { AnalyticsComponent } from './analytics/analytics.component';

// Crucix intelligence components
import { IntelligenceComponent } from './intelligence/intelligence.component';
import { OpportunitiesComponent } from './opportunities/opportunities.component';
import { SourcesComponent } from './sources/sources.component';
import { ExplorerComponent } from './explorer/explorer.component';
import { BdIntelligenceComponent } from './bd-intelligence/bd-intelligence.component';
import { SearchComponent } from './search/search.component';
import { FormsModule } from '@angular/forms';

@NgModule({
  declarations: [
    AnalyticsComponent,
    ECommerceComponent,
    IntelligenceComponent,
    OpportunitiesComponent,
    SourcesComponent,
    ExplorerComponent,
    BdIntelligenceComponent,
    SearchComponent,
  ],
  imports: [
    CommonModule,
    HttpClientModule,
    FormsModule,
    DashboardRoutingModule,
    MatModule,
  ]
})
export class DashboardModule { }
