import { Component, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatModule } from 'src/app/appModules/mat.module';

import {
  ChartComponent,
  ApexAxisChartSeries,
  ApexChart,
  ApexXAxis,
  ApexDataLabels,
  ApexTitleSubtitle,
  ApexStroke,
  ApexGrid,
  ApexFill,
  ApexMarkers,
  ApexYAxis,
  ApexPlotOptions,
  ApexTooltip,
  ApexNonAxisChartSeries,
  ApexResponsive,
} 

from "ng-apexcharts";

export type ChartOptions = {
  series: ApexAxisChartSeries | ApexNonAxisChartSeries;
  responsive: ApexResponsive[];
  chart: ApexChart;
  xaxis: ApexXAxis;
  dataLabels: ApexDataLabels;
  grid: ApexGrid;
  stroke: ApexStroke;
  title: ApexTitleSubtitle;
  fill: ApexFill,
  markers: ApexMarkers,
  colors: string[];
  plotOptions: ApexPlotOptions;
  yaxis: ApexYAxis;
  tooltip: ApexTooltip;
  labels: any;
};

@Component({
  selector: 'app-apex-charts',
  standalone: true,
  imports: [CommonModule, MatModule],
  templateUrl: './apex-charts.component.html',
  styleUrl: './apex-charts.component.scss'
})

export class ApexChartsComponent {
  @ViewChild("chart") chart: ChartComponent;

  public AreachartOptions: Partial<ChartOptions>;
  public LinechartOptions: Partial<ChartOptions>;
  public BarchartOptions: Partial<ChartOptions>;
  public MultiColumnchartOptions: Partial<ChartOptions>;
  public SimplePiechartOptions: Partial<ChartOptions>;
  public SimpleDonutchartOptions: Partial<ChartOptions>;


  constructor() {
    // chart 1
    this.AreachartOptions = {
      series
      : [
        {
          name: "Desktops",
          data: [10, 41, 35, 51, 49, 62, 69, 91, 148]
        }
      ],
      chart: {
        height: 350,
        type: "area",
        zoom: { 
          enabled: false
        },
        toolbar: {
          show: !1
        },
      },
      colors: ["#495bbd"],
      dataLabels: {
        enabled: false
      },
      stroke: {
        curve: "smooth"
      },
      grid: {
        show: true,
        borderColor: 'rgba(0, 0, 0, 0.15)',
        strokeDashArray: 4,
      },
      xaxis: {
        categories: [
          "Jan",
          "Feb",
          "Mar",
          "Apr",
          "May",
          "Jun",
          "Jul",
          "Aug",
          "Sep"
        ]
      },
      tooltip: {
        theme: "dark",
      }
    };


    // chart 2
    this.LinechartOptions = {
      series: [
        {
          name: "Desktops",
          data: [14, 3, 10, 9, 29, 19, 22, 9, 12, 7, 19, 5]
        }
      ],
      chart: {
        height: 350,
        type: "line",
        zoom: {
          enabled: false
        },
        toolbar: {
          show: !1
        },
      },
      dataLabels: {
        enabled: false
      },
      stroke: {
        width: 5,
        curve: "smooth"
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#ee226e"],
          shadeIntensity: 1,
          type: "horizontal",
          opacityFrom: 1,
          opacityTo: 1,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#495bbd"],
      markers: {
        size: 4,
        colors: ["#7343be"],
        strokeColors: "#fff",
        strokeWidth: 2,
        hover: {
          size: 7,
        }
      },
      grid: {
        show: true,
        borderColor: 'rgba(0, 0, 0, 0.15)',
        strokeDashArray: 4,
      },
      xaxis: {
        categories: [
          "Jan",
          "Feb",
          "Mar",
          "Apr",
          "May",
          "Jun",
          "Jul",
          "Aug",
          "Sep",
          "oct",
          "Nov",
          "Dec"
        ]
      },
      tooltip: {
        theme: "dark",
      }
    };


    // chart 3
    this.BarchartOptions = {
      series: [
        {
          name: "Sales",
          data: [14, 3, 10, 9, 29, 19, 22, 9, 12, 7, 19, 5]
        }
      ],
      chart: {
        height: 350,
        type: "bar",
        zoom: {
          enabled: false
        },
        toolbar: {
          show: !1
        },
      },
      dataLabels: {
        enabled: false
      },
      stroke: {
        width: 0,
        curve: "smooth",
        colors: ["transparent"],
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#495bbd"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 1,
          opacityTo: 1,
          stops: [0, 100, 100, 100]
        }
      },
      grid: {
        show: true,
        borderColor: 'rgba(0, 0, 0, 0.15)',
        strokeDashArray: 4,
      },
      colors: ["#495bbd"],
      markers: {
        size: 4,
        colors: ["#fe6225"],
        strokeColors: "#fff",
        strokeWidth: 2,
        hover: {
          size: 7,
        }
      },
      xaxis: {
        categories: [
          "Jan",
          "Feb",
          "Mar",
          "Apr",
          "May",
          "Jun",
          "Jul",
          "Aug",
          "Sep",
          "oct",
          "Nov",
          "Dec"
        ]
      },
      tooltip: {
        theme: "dark",
      }
    };


    // chart 4
    this.MultiColumnchartOptions = {
      series: [
        {
          name: "Net Profit",
          data: [44, 55, 57, 56, 61, 58, 63, 60, 66]
        },
        {
          name: "Revenue",
          data: [76, 85, 101, 98, 87, 105, 91, 114, 94]
        },
        {
          name: "Free Cash Flow",
          data: [35, 41, 36, 26, 45, 48, 52, 53, 41]
        }
      ],
      chart: {
        type: "bar",
        height: 350,
        toolbar: {
          show: !1
        },
      },
      plotOptions: {
        bar: {
          horizontal: false,
          columnWidth: "55%",
          //endingShape: "rounded"
        }
      },
      colors: ["#ee226e", "#7343be", "#03b3f5"],
      dataLabels: {
        enabled: false
      },
      stroke: {
        show: true,
        width: 4,
        colors: ["transparent"]
      },
      xaxis: {
        categories: [
          "Feb",
          "Mar",
          "Apr",
          "May",
          "Jun",
          "Jul",
          "Aug",
          "Sep",
          "Oct"
        ]
      },
      yaxis: {
        title: {
          text: "$ (thousands)"
        }
      },
      fill: {
        opacity: 1
      },
      grid: {
        show: true,
        borderColor: 'rgba(0, 0, 0, 0.15)',
        strokeDashArray: 4,
      },
      tooltip: {
        theme: "dark",
        y: {
          formatter: function(val) {
            return "$ " + val + " thousands";
          }
        }
      }
    };


    // chart 5
    this.SimplePiechartOptions = {
      series: [44, 55, 13, 43, 22],
      chart: {
        height: 380,
        type: "pie"
      },
      labels: ["Team A", "Team B", "Team C", "Team D", "Team E"],
      colors: ["#ee226e", "#7343be", "#03b3f5", "#fe6225", "#00a294"],
      responsive: [
        {
          breakpoint: 480,
          options: {
            chart: {
              width: 200
            },
            legend: {
              position: "bottom"
            }
          }
        }
      ]
    };

    // chart 5
    this.SimpleDonutchartOptions = {
      series: [44, 55, 13, 43, 22],
      chart: {
        height: 380,
        type: "donut"
      },
      labels: ["Team A", "Team B", "Team C", "Team D", "Team E"],
      colors: ["#ee226e", "#7343be", "#03b3f5", "#fe6225", "#00a294"],
      responsive: [
        {
          breakpoint: 480,
          options: {
            chart: {
              width: 200
            },
            legend: {
              position: "bottom"
            }
          }
        }
      ]
    };




  }
}

