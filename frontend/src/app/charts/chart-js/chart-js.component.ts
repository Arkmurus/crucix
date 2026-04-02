import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatModule } from 'src/app/appModules/mat.module';


@Component({
  selector: 'app-chart-js',
  standalone: true,
  imports: [CommonModule, MatModule],
  templateUrl: './chart-js.component.html',
  styleUrl: './chart-js.component.scss'
})

export class ChartJsComponent {

barChartOptions: any;
barChartData: any;

lineChartOptions: any;
lineChartData: any;

PieChartOptions: any;
PieChartData: any;

DoughnutChartOptions: any;
DoughnutChartData: any;




  ngOnInit() {

    // chart 1
    this.barChartData = {
      labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul'],
      datasets: [{
          label: 'Electronics',
          data: [18, 25, 14, 12, 17, 8, 10],
          backgroundColor: [
              '#495bbd'
          ],
          borderColor: [
              '#495bbd'
          ],
          borderWidth: 1
        },
        {
          label: 'Furniture',
          data: [12, 30, 16, 23, 8, 14, 11],
          backgroundColor: [
              '#fe6225'
          ],
          borderColor: [
              '#fe6225'
          ],
          borderWidth: 1
       }]
    }
    this.barChartOptions= {
      maintainAspectRatio: false,
      barPercentage: 0.5,
      categoryPercentage: 0.8,
      plugins: {
        legend: {
          display: true,
        }
      },
      scales: {
        y: {
          beginAtZero: true
        }
      }
    }


    // chart 2
    this.lineChartData = {
      labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'],
      datasets: [{
          label: 'Google',
          data: [6, 20, 14, 12, 17, 8, 10],
          backgroundColor: [
              '#923eb9'
          ],
          borderColor: [
              '#923eb9'
          ],
          borderWidth: 4,
          tension: 0.4,
          pointBackgroundColor: 'transparent',
          pointBorderColor: 'transparent',
          pointHoverBackgroundColor: 'transparent',
        },
        {
          label: 'Facebook',
          data: [5, 30, 16, 23, 8, 14, 11],
          backgroundColor: [
              '#18bb6b'
          ],
          fill: {
            target: 'origin',
            above: 'rgb(21 202 32 / 20%)',   // Area will be red above the origin
            //below: 'rgb(21 202 32 / 100%)'   // And blue below the origin
            }, 
          borderColor: [
              '#18bb6b'
          ],
          borderWidth: 4,
          tension: 0.4,
          pointBackgroundColor: 'transparent',
          pointBorderColor: 'transparent',
          pointHoverBackgroundColor: 'transparent',
       }]
    }
    this.lineChartOptions= {
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
        }
      },
      scales: {
        y: {
          beginAtZero: true
        }
      }
    }


     // chart 3
     this.PieChartData = {
      labels: ['Red', 'Blue', 'Yellow', 'Green', 'Purple', 'Orange'],
      datasets: [{
        data: [12, 19, 3, 5, 2, 3],
        backgroundColor: [
            '#0d6efd',
            '#6f42c1',
            '#d63384',
            '#fd7e14',
            '#15ca20',
            '#0dcaf0'
        ],
        borderWidth: 1.5
    }]
    }
    this.PieChartOptions= {
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position:'bottom',
					display: true,
        }
      },
     
    }


     // chart 4
     this.DoughnutChartData = {
      labels: ['Red', 'Blue', 'Yellow', 'Green', 'Purple', 'Orange'],
      datasets: [{
        data: [12, 19, 3, 5, 2, 3],
        backgroundColor: [
            '#0d6efd',
            '#6f42c1',
            '#d63384',
            '#fd7e14',
            '#15ca20',
            '#0dcaf0'
        ],
        borderWidth: 1.5
    }]
    }
    this.DoughnutChartOptions= {
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position:'bottom',
					display: true,
        }
      },
     
    }
    

    
  }


}
