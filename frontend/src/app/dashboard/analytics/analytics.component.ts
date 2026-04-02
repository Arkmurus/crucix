import { Component, OnInit, ViewChild } from '@angular/core';
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
  ApexLegend,
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
  labels: string[];
  legend: ApexLegend;
};

@Component({
  selector: 'app-analytics',
  templateUrl: './analytics.component.html',
  styleUrls: ['./analytics.component.scss']
})
export class AnalyticsComponent implements OnInit {

  @ViewChild("chart") chart: ChartComponent;

  public AnalyticsChart1: Partial<ChartOptions>;
  public AnalyticsChart2: Partial<ChartOptions>;
  public AnalyticsChart3: Partial<ChartOptions>;
  public AnalyticsChart4: Partial<ChartOptions>;
  public AnalyticsChart5: Partial<ChartOptions>;
  public AnalyticsChart6: Partial<ChartOptions>;
  public AnalyticsChart7: Partial<ChartOptions>;
  public AnalyticsChart8: Partial<ChartOptions>;
  public AnalyticsChart9: Partial<ChartOptions>;

  constructor() {



    // chart 1
    this.AnalyticsChart1 = {
      series
        : [
          {
            name: "Revenu",
            data: [25, 66, 41, 59, 25, 44, 12, 36, 9, 21]
          }
        ],
      chart: {
        width: 130,
        height: 50,
        type: "bar",
        zoom: {
          enabled: false
        },
        toolbar: {
          show: !1
        },
        sparkline: {
          enabled: !0
        }
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#a52db7"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 1,
          opacityTo: 1,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#a52db7"],
      plotOptions: {
        bar: {
          horizontal: false,
          borderRadius: 2.5,
          //borderRadiusApplication: 'around',
          // borderRadiusWhenStacked: 'last',
          //distributed: true,
          columnWidth: '55%',
        }
      },
      dataLabels: {
        enabled: false
      },
      stroke: {
        width: 1,
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
    this.AnalyticsChart2 = {
      series
        : [
          {
            name: "Revenu",
            data: [25, 66, 41, 59, 25, 44, 12, 36, 9, 21]
          }
        ],
      chart: {
        width: 130,
        height: 50,
        type: "bar",
        zoom: {
          enabled: false
        },
        toolbar: {
          show: !1
        },
        sparkline: {
          enabled: !0
        }
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#ff4081"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 1,
          opacityTo: 1,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#ff4081"],
      plotOptions: {
        bar: {
          horizontal: false,
          borderRadius: 2.5,
          //borderRadiusApplication: 'around',
          // borderRadiusWhenStacked: 'last',
          //distributed: true,
          columnWidth: '55%',
        }
      },
      dataLabels: {
        enabled: false
      },
      stroke: {
        width: 1,
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




    // chart 3
    this.AnalyticsChart3 = {
      series
        : [
          {
            name: "Revenu",
            data: [25, 66, 41, 59, 25, 44, 12, 36, 9, 21]
          }
        ],
      chart: {
        width: 130,
        height: 50,
        type: "bar",
        zoom: {
          enabled: false
        },
        toolbar: {
          show: !1
        },
        sparkline: {
          enabled: !0
        }
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#00a294"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 1,
          opacityTo: 1,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#00a294"],
      plotOptions: {
        bar: {
          horizontal: false,
          borderRadius: 2.5,
          //borderRadiusApplication: 'around',
          // borderRadiusWhenStacked: 'last',
          //distributed: true,
          columnWidth: '55%',
        }
      },
      dataLabels: {
        enabled: false
      },
      stroke: {
        width: 1,
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



    // chart 4
    this.AnalyticsChart4 = {
      series
        : [
          {
            name: "Revenu",
            data: [25, 66, 41, 59, 25, 44, 12, 36, 9, 21]
          }
        ],
      chart: {
        foreColor: "#9ba7b2",
        width: 130,
        height: 50,
        type: "bar",
        zoom: {
          enabled: false
        },
        toolbar: {
          show: !1
        },
        sparkline: {
          enabled: !0
        }
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#25a0f4"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 1,
          opacityTo: 1,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#25a0f4"],
      plotOptions: {
        bar: {
          horizontal: false,
          borderRadius: 2.5,
          //borderRadiusApplication: 'around',
          // borderRadiusWhenStacked: 'last',
          //distributed: true,
          columnWidth: '55%',
        }
      },
      dataLabels: {
        enabled: false
      },
      stroke: {
        width: 1,
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



    // chart 5
    this.AnalyticsChart5 = {
      series: [
        {
          name: "Views",
          data: [0, 7, 18, 10, 18, 14, 40, 30, 56, 13, 18, 2]
        }
      ],
      chart: {
        foreColor: "#9ba7b2",
        height: 370,
        type: "area",
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
        width: 4,
        curve: "smooth"
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#6a11cb"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 0.9,
          opacityTo: 0.1,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#495bbd"],
      markers: {
        size: 5,
        colors: ["#fff"],
        strokeColors: "#7343be",
        strokeWidth: 2,
        hover: {
          size: 7,
        }
      },
      legend: {
        show: false,
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
          "Oct",
          "Nov",
          "Dec"
        ]
      },
      tooltip: {
        theme: "dark",
      }
    };



    // chart 6
    this.AnalyticsChart6 = {
      series
        : [
          {
            name: "New Visitors",
            data: [25, 66, 41, 59, 25, 44, 12, 36, 9, 21]
          },
          {
            name: "Old Visitors",
            data: [25, 66, 41, 59, 25, 44, 12, 36, 9, 21]
          }
        ],
      chart: {
        foreColor: "#9ba7b2",
        height: 260,
        type: "bar",
        stacked: true,
        zoom: {
          enabled: false
        },
        toolbar: {
          show: !1
        },
        sparkline: {
          enabled: !1
        }
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#25a0f4", "#eee"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 1,
          opacityTo: 1,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#25a0f4", "#eee"],
      plotOptions: {
        bar: {
          horizontal: false,
          borderRadius: 2.5,
          //borderRadiusApplication: 'around',
          // borderRadiusWhenStacked: 'last',
          //distributed: true,
          columnWidth: '45%',
        }
      },
      dataLabels: {
        enabled: false
      },
      legend: {
        show: false,
      },
      stroke: {
        width: 1,
        curve: "smooth"
      },
      grid: {
        show: false,
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
          "Oct"
        ]
      },
      tooltip: {
        theme: "dark",
      }
    };


    // chart 7
    this.AnalyticsChart7 = {
      labels: ["Completed", "Pending", "Received"],
      series: [44, 55, 41],
      chart: {
        height: 280,
        type: "donut"
      },
      dataLabels: {
        enabled: false
      },
      colors: ["#a52db7", "#00a294", "#ee226e"],
      legend: {
        show: false,
        position: 'bottom',
        formatter: function (val, opts) {
          return val + " - " + opts.w.globals.series[opts.seriesIndex];
        }
      },
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



    // chart 8
    this.AnalyticsChart8 = {
      labels: ["Completed", "Pending", "Received"],
      series: [120, 250, 568],
      chart: {
        height: 280,
        type: "pie"
      },
      dataLabels: {
        enabled: false
      },
      colors: ["#25a0f4", "#57b85c", "#fe6225"],
      legend: {
        show: false,
        position: 'bottom',
        formatter: function (val, opts) {
          return val + " - " + opts.w.globals.series[opts.seriesIndex];
        }
      },
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
      ],
      tooltip: {
        theme: "dark",
      }
    };



    // chart 9
    this.AnalyticsChart9 = {
      series: [
        {
          name: "Views",
          data: [0, 7, 18, 10, 18, 14, 40, 15, 5]
        }
      ],
      chart: {
        foreColor: "#9ba7b2",
        height: 370,
        type: "area",
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
        width: 3,
        curve: "straight"
      },
      fill: {
        type: "gradient",
        gradient: {
          shade: "dark",
          gradientToColors: ["#f9f047"],
          shadeIntensity: 1,
          type: "vertical",
          opacityFrom: 0.9,
          opacityTo: 0.3,
          stops: [0, 100, 100, 100]
        }
      },
      colors: ["#0fd850"],
      markers: {
        size: 5,
        colors: ["#fff"],
        strokeColors: "#0fd850",
        strokeWidth: 2,
        hover: {
          size: 7,
        }
      },
      legend: {
        show: false,
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





  }

  ngOnInit(): void {
    // $.getScript('./assets/js/analytics-dashboard.js');
  }

}
