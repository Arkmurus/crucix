import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';

import { IntelligenceComponent } from './intelligence/intelligence.component';
import { OpportunitiesComponent } from './opportunities/opportunities.component';
import { SourcesComponent } from './sources/sources.component';
import { ExplorerComponent } from './explorer/explorer.component';

const routes: Routes = [
  {
    path: '',
    children: [
      { path: 'brief',         component: IntelligenceComponent, data: { title: 'Intelligence Brief' } },
      { path: 'opportunities', component: OpportunitiesComponent, data: { title: 'Opportunity Pipeline' } },
      { path: 'sources',       component: SourcesComponent,      data: { title: 'Source Health' } },
      { path: 'explorer',      component: ExplorerComponent,     data: { title: 'Intelligence Explorer' } },
    ]
  }
];

@NgModule({
  imports: [RouterModule.forChild(routes)],
  exports: [RouterModule]
})
export class DashboardRoutingModule { }
