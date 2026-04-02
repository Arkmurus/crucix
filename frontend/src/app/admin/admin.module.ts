import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminRoutingModule } from './admin-routing.module';
import { UsersComponent } from './users/users.component';
import { MatModule } from '../appModules/mat.module';

@NgModule({
  declarations: [UsersComponent],
  imports: [CommonModule, FormsModule, AdminRoutingModule, MatModule]
})
export class AdminModule { }
