import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { ChartJsComponent } from './chart-js/chart-js.component';
import { ApexChartsComponent } from './apex-charts/apex-charts.component';

const routes: Routes = [
  {
    path: '',
    children: [
      {
        path: 'chart-js',
        component: ChartJsComponent,
        data: {
          title: 'chart-js'
        }
      },
      {
        path: 'apex-chart',
        component: ApexChartsComponent,
        data: {
          title: 'apex-chart'
        }
      },
    ]
  }
];

@NgModule({
  imports: [RouterModule.forChild(routes)],
  exports: [RouterModule]
})
export class ChartsRoutingModule { }
