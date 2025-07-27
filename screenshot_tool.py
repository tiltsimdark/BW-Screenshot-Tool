import tkinter as tk
from tkinter import filedialog
import os
import json
import time
from PIL import ImageGrab
from datetime import datetime

class ScreenshotTool:
    SETTINGS_FILE = "screenshot_settings.json"
    
    def __init__(self, root):
        self.root = root
        self.root.title("Screenshot Tool")
        
        # Default settings
        self.settings = {
            "master_folder": "",
            "window_geometry": None
        }
        self.subfolder = ""
        self.selected_area = None
        
        # Notification variables
        self.notification = None
        self.notification_id = None
        
        # Load settings
        self.load_settings()
        
        # Main GUI elements
        self.create_main_gui()
        
    def load_settings(self):
        """Load settings from JSON file"""
        try:
            if os.path.exists(self.SETTINGS_FILE):
                with open(self.SETTINGS_FILE, 'r') as f:
                    loaded_settings = json.load(f)
                    self.settings.update(loaded_settings)
        except Exception as e:
            print(f"Error loading settings: {e}")

    def save_settings(self):
        """Save settings to JSON file"""
        try:
            with open(self.SETTINGS_FILE, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def create_main_gui(self):
        """Create the main application interface"""
        # Clear any existing widgets
        for widget in self.root.winfo_children():
            widget.destroy()
        
        # Main container frame
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(expand=True, fill=tk.BOTH)
        
        # Folder name entry
        tk.Label(main_frame, text="Folder Name:").pack(pady=(10, 0))
        self.folder_entry = tk.Entry(main_frame, width=30)
        self.folder_entry.pack(pady=(0, 10))
        
        # Area selection frame
        area_frame = tk.Frame(main_frame)
        area_frame.pack(pady=10)
        
        # Select area button centered
        tk.Button(area_frame, text="Select Area", command=self.start_area_selection).pack()
        
        # Coordinates display and edit frame
        coord_frame = tk.Frame(main_frame)
        coord_frame.pack(pady=10)
        
        # Coordinate entry fields
        self.coord_vars = []
        labels = ['X:', 'Y:', 'Width:', 'Height:']
        for i, label in enumerate(labels):
            tk.Label(coord_frame, text=label).grid(row=0, column=i*2, padx=2)
            var = tk.StringVar()
            entry = tk.Entry(coord_frame, textvariable=var, width=5)
            entry.grid(row=0, column=i*2+1, padx=2)
            self.coord_vars.append(var)
        
        # Update coordinates button
        tk.Button(coord_frame, text="Update", command=self.update_coords).grid(row=0, column=8, padx=5)
        
        # Take screenshot button centered
        tk.Button(main_frame, text="Take Screenshot", command=self.take_screenshot).pack(pady=10)
        
        # Settings button centered
        tk.Button(main_frame, text="Settings", command=self.show_settings).pack(pady=10)
        
    def show_notification(self, message):
        """Show a temporary notification in the bottom right corner"""
        # Remove any existing notification
        if self.notification:
            self.notification.destroy()
            if self.notification_id:
                self.root.after_cancel(self.notification_id)
        
        # Create notification label
        self.notification = tk.Label(
            self.root,
            text=message,
            bg='lightgreen',
            padx=10,
            pady=5,
            relief=tk.RAISED,
            borderwidth=1
        )
        
        # Position in bottom right
        self.notification.place(
            relx=1.0,
            rely=1.0,
            anchor='se',
            x=-10,
            y=-10
        )
        
        # Schedule removal after 3 seconds
        self.notification_id = self.root.after(3000, self.remove_notification)
    
    def remove_notification(self):
        """Remove the current notification"""
        if self.notification:
            self.notification.destroy()
            self.notification = None
            self.notification_id = None
    
    def show_settings(self):
        """Show the settings menu with folder browsing"""
        # Save window geometry before switching to settings
        self.settings["window_geometry"] = self.root.geometry()
        self.save_settings()
        
        for widget in self.root.winfo_children():
            widget.destroy()
        
        # Main container frame
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(expand=True, fill=tk.BOTH)
        
        # Master folder selection
        tk.Label(main_frame, text="Master Folder Path:").pack(pady=(10, 0))
        
        folder_frame = tk.Frame(main_frame)
        folder_frame.pack(pady=(0, 10), fill=tk.X)
        
        self.master_entry = tk.Entry(folder_frame, width=40)
        self.master_entry.insert(0, self.settings["master_folder"])
        self.master_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        
        # Browse button
        tk.Button(folder_frame, text="Browse", command=self.browse_folder).pack(side=tk.LEFT, padx=5)
        
        # Back button centered
        tk.Button(main_frame, text="Back to Main Menu", command=self.return_to_main).pack(pady=20)
    
    def return_to_main(self):
        """Return to main menu from settings"""
        # Save the master folder setting
        self.settings["master_folder"] = self.master_entry.get()
        self.save_settings()
        self.create_main_gui()
        
        # Restore window geometry if available
        if self.settings.get("window_geometry"):
            self.root.geometry(self.settings["window_geometry"])
    
    def browse_folder(self):
        """Open folder browser dialog"""
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.master_entry.delete(0, tk.END)
            self.master_entry.insert(0, folder_selected)
            self.show_notification(f"Master folder will be saved when you return to main menu")
    
    def start_area_selection(self):
        """Start the area selection process"""
        self.root.withdraw()
        
        # Create a fullscreen transparent window
        self.selector = tk.Toplevel()
        self.selector.attributes('-fullscreen', True)
        self.selector.attributes('-alpha', 0.3)
        self.selector.configure(bg='black')
        
        # Create canvas for drawing
        self.canvas = tk.Canvas(self.selector, cursor="cross", bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Initialize selection variables
        self.start_x = None
        self.start_y = None
        self.rect = None
        
        # Bind events
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.selector.bind("<Escape>", self.cancel_selection)
        
        # Center the selection window
        self.selector.update_idletasks()
        width = self.selector.winfo_width()
        height = self.selector.winfo_height()
        x = (self.selector.winfo_screenwidth() // 2) - (width // 2)
        y = (self.selector.winfo_screenheight() // 2) - (height // 2)
        self.selector.geometry(f'+{x}+{y}')
    
    def on_press(self, event):
        """Handle mouse button press"""
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, 
            self.start_x, self.start_y, 
            outline='red', width=2
        )
    
    def on_drag(self, event):
        """Handle mouse drag"""
        if self.rect:
            self.canvas.coords(
                self.rect, 
                self.start_x, self.start_y, 
                event.x, event.y
            )
    
    def on_release(self, event):
        """Handle mouse button release"""
        if self.rect:
            end_x = event.x
            end_y = event.y
            
            # Calculate selected area
            x1 = min(self.start_x, end_x)
            y1 = min(self.start_y, end_y)
            x2 = max(self.start_x, end_x)
            y2 = max(self.start_y, end_y)
            
            self.selected_area = (x1, y1, x2-x1, y2-y1)
            self.update_coord_display()
            self.show_notification("Area selected successfully")
            
            # Clean up
            self.selector.destroy()
            self.root.deiconify()
    
    def update_coord_display(self):
        """Update the coordinate entry fields with current selection"""
        if self.selected_area:
            for i, val in enumerate(self.selected_area):
                self.coord_vars[i].set(str(val))
    
    def update_coords(self):
        """Update selected area from manual coordinate entry"""
        try:
            coords = [int(var.get()) for var in self.coord_vars]
            if len(coords) == 4:
                self.selected_area = tuple(coords)
                self.show_notification("Coordinates updated successfully")
            else:
                self.show_notification("Please enter all four values")
        except ValueError:
            self.show_notification("Please enter valid numbers")
    
    def cancel_selection(self, event=None):
        """Cancel the selection process"""
        self.selector.destroy()
        self.root.deiconify()
        self.show_notification("Area selection canceled")
    
    def take_screenshot(self):
        """Take a screenshot of the selected area"""
        if not self.selected_area:
            self.show_notification("Please select an area first")
            return
            
        self.subfolder = self.folder_entry.get().strip()
        if not self.subfolder:
            self.show_notification("Please enter a folder name")
            return
            
        try:
            # Create the full path
            full_path = os.path.join(self.settings["master_folder"], self.subfolder)
            os.makedirs(full_path, exist_ok=True)
            
            # Hide window before taking screenshot
            self.root.withdraw()
            time.sleep(0.2)  # Small delay to ensure window is hidden
            
            # Take screenshot
            screenshot = ImageGrab.grab(bbox=(
                self.selected_area[0],
                self.selected_area[1],
                self.selected_area[0] + self.selected_area[2],
                self.selected_area[1] + self.selected_area[3]
            ))
            
            # Save with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(full_path, f"screenshot_{timestamp}.png")
            screenshot.save(filename)
            
            self.show_notification(f"Screenshot saved to: {filename}")
        except Exception as e:
            self.show_notification(f"Error: {str(e)}")
        finally:
            self.root.deiconify()

if __name__ == "__main__":
    root = tk.Tk()
    
    # Set default window size and center
    window_width = 400
    window_height = 350
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width // 2) - (window_width // 2)
    y = (screen_height // 2) - (window_height // 2)
    root.geometry(f'{window_width}x{window_height}+{x}+{y}')
    
    app = ScreenshotTool(root)
    root.mainloop()